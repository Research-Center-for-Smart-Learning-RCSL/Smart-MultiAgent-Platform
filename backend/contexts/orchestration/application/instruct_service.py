"""Instruct chain service (G.7 — R15.15–R15.17).

Dispatches instruct messages between agents with loop detection (cycle in
``path``), depth cap, per-wakeup count cap, and wall-clock budget.

SoC:
- Chain validation is pure logic in ``_validate_chain``.
- DB access → ``infrastructure.repositories.InstructionRepository``
- A2A delivery → ``A2AService``
- Audit → ``shared_kernel.audit``
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.orchestration.application.a2a_service import A2AService
from contexts.orchestration.domain.errors import (
    InstructBudgetExceeded,
    InstructLoopDetected,
)
from contexts.orchestration.domain.models import (
    INSTRUCT_MAX_CHAIN_DEPTH,
    INSTRUCT_MAX_CHAIN_DEPTH_HARD,
    INSTRUCT_MAX_CHAIN_SECONDS,
    INSTRUCT_MAX_PER_WAKEUP,
    A2AEnvelope,
    A2AMessageType,
    Instruction,
    InstructionState,
)
from contexts.orchestration.infrastructure.metrics import INSTRUCT_CHAIN_DEPTH
from contexts.orchestration.infrastructure.repositories import InstructionRepository
from shared_kernel import audit


class InstructService:
    """Application-level instruct chain management (G.7)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._instructions = InstructionRepository(db)
        self._a2a = A2AService(db)

    async def issue(
        self,
        *,
        issuer_agent_id: uuid.UUID,
        target_agent_id: uuid.UUID,
        payload: dict[str, Any],
        chain_id: uuid.UUID | None = None,
        parent_path: tuple[uuid.UUID, ...] = (),
        wakeup_started_at: datetime | None = None,
        max_chain_depth: int = INSTRUCT_MAX_CHAIN_DEPTH,
        max_per_wakeup: int = INSTRUCT_MAX_PER_WAKEUP,
        max_chain_seconds: int = INSTRUCT_MAX_CHAIN_SECONDS,
    ) -> Instruction:
        """Issue an instruct to target_agent_id with full chain validation."""
        chain_id = chain_id or uuid.uuid4()
        new_path = (*parent_path, issuer_agent_id)
        depth = len(new_path)

        # Clamp project-configurable depth to hard cap.
        effective_depth = min(max_chain_depth, INSTRUCT_MAX_CHAIN_DEPTH_HARD)

        # R15.16 rule 1: cycle detection.
        if target_agent_id in new_path:
            instruction = await self._instructions.insert(
                id=uuid.uuid4(),
                chain_id=chain_id,
                path=list(new_path),
                depth=depth,
                issuer_agent_id=issuer_agent_id,
                target_agent_id=target_agent_id,
                payload=payload,
                state=InstructionState.REJECTED_LOOP,
            )
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="instruct.rejected_loop",
                    resource_type="instruction",
                    resource_id=instruction.id,
                    metadata={
                        "chain_id": str(chain_id),
                        "path": [str(p) for p in new_path],
                        "target_agent_id": str(target_agent_id),
                    },
                ),
            )
            # The rejection INSERT and audit are in the caller's ambient
            # transaction. If the caller rolls back on InstructLoopDetected the
            # records are lost; loop detection itself is always correct regardless.
            raise InstructLoopDetected(f"cycle detected: {target_agent_id} already in path {new_path}")

        # R15.16 rule 2: depth cap.
        if depth >= effective_depth:
            raise InstructBudgetExceeded(f"chain depth {depth} >= max {effective_depth}")

        # R15.16 rule 3: per-wakeup count cap.
        if wakeup_started_at:
            count = await self._instructions.count_issued_by_agent_since(
                issuer_agent_id,
                wakeup_started_at,
            )
            if count >= max_per_wakeup:
                raise InstructBudgetExceeded(
                    f"agent {issuer_agent_id} issued {count} instructs " f"this wakeup (max {max_per_wakeup})"
                )

        # R15.16 rule 4: wall-clock chain budget.
        chain_start = await self._instructions.get_chain_start_time(chain_id)
        if chain_start:
            elapsed = (datetime.now(UTC) - chain_start).total_seconds()
            if elapsed >= max_chain_seconds:
                raise InstructBudgetExceeded(
                    f"chain {chain_id} elapsed {elapsed:.1f}s >= max {max_chain_seconds}s"
                )

        # All checks passed — record and dispatch.
        instruction_id = uuid.uuid4()
        instruction = await self._instructions.insert(
            id=instruction_id,
            chain_id=chain_id,
            path=list(new_path),
            depth=depth,
            issuer_agent_id=issuer_agent_id,
            target_agent_id=target_agent_id,
            payload=payload,
        )

        INSTRUCT_CHAIN_DEPTH.observe(depth)

        envelope = A2AEnvelope(
            id=uuid.uuid4(),
            from_agent=issuer_agent_id,
            to_agent=str(target_agent_id),
            workflow_run_id=None,
            type=A2AMessageType.INSTRUCT,
            payload={
                "instruction_id": str(instruction_id),
                "chain_id": str(chain_id),
                "path": [str(p) for p in new_path],
                "depth": depth,
                **payload,
            },
            correlation_id=uuid.uuid4(),
            created_at=datetime.now(UTC),
        )
        await self._a2a.send(envelope=envelope)

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="instruct.issued",
                resource_type="instruction",
                resource_id=instruction_id,
                metadata={
                    "chain_id": str(chain_id),
                    "issuer_agent_id": str(issuer_agent_id),
                    "target_agent_id": str(target_agent_id),
                    "depth": depth,
                    "path": [str(p) for p in new_path],
                },
            ),
        )

        return instruction

    async def mark_delivered(self, instruction_id: uuid.UUID) -> None:
        await self._instructions.update_state(
            instruction_id,
            InstructionState.DELIVERED,
        )

    async def mark_completed(self, instruction_id: uuid.UUID) -> None:
        await self._instructions.update_state(
            instruction_id,
            InstructionState.COMPLETED,
        )

    async def mark_timeout(self, instruction_id: uuid.UUID) -> None:
        await self._instructions.update_state(
            instruction_id,
            InstructionState.TIMEOUT,
        )

    async def mark_failed(self, instruction_id: uuid.UUID) -> None:
        """Record a provider/turn failure — distinct from a deadline timeout.

        The DB ``instruction_state`` enum has no ``failed`` member (adding one
        needs a migration plus a resume-port mapping change), so the row
        settles as TIMEOUT — which the workflow resume task already maps to
        the ``failure`` port — and the actual cause is preserved as an
        ``instruct.failed`` audit event.
        """
        await self._instructions.update_state(
            instruction_id,
            InstructionState.TIMEOUT,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="instruct.failed",
                resource_type="instruction",
                resource_id=instruction_id,
                metadata={"reason": "turn_failed"},
            ),
        )

    async def get_instruction(self, instruction_id: uuid.UUID) -> Instruction | None:
        return await self._instructions.get(instruction_id)

    async def list_for_chain(self, chain_id: uuid.UUID) -> list[Instruction]:
        return await self._instructions.list_for_chain(chain_id)


__all__ = ["InstructService"]
