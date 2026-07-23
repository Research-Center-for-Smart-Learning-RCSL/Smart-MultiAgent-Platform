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

from contexts.agents.interfaces.facade import AgentsFacade
from contexts.orchestration.application import instruct_chain
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
        self._agents = AgentsFacade(db)

    async def issue(
        self,
        *,
        issuer_agent_id: uuid.UUID,
        target_agent_id: uuid.UUID,
        payload: dict[str, Any],
        workflow_run_id: uuid.UUID | None = None,
        chain_id: uuid.UUID | None = None,
        parent_path: tuple[uuid.UUID, ...] = (),
        wakeup_started_at: datetime | None = None,
        max_chain_depth: int = INSTRUCT_MAX_CHAIN_DEPTH,
        max_per_wakeup: int = INSTRUCT_MAX_PER_WAKEUP,
        max_chain_seconds: int = INSTRUCT_MAX_CHAIN_SECONDS,
    ) -> Instruction:
        """Issue an instruct to target_agent_id with full chain validation.

        ``workflow_run_id`` is the originating run: it is stamped onto the
        delivered envelope for attribution ([R15.23]) and is the context
        A2AService derives rule 3a from (both agents must be run participants).
        For a workflow-originated instruct it is also the [R15.16] rule 3
        issuing window when no ``wakeup_started_at`` is supplied.
        """
        # F-25 fallback: an instruct issued from inside a delivered instruct's
        # inline turn (no explicit chain supplied) inherits the ambient chain so
        # the loop/depth/budget guards continue rather than reset to a fresh chain.
        if chain_id is None and not parent_path:
            ctx_chain_id, ctx_parent_path = instruct_chain.current()
            if ctx_chain_id is not None:
                chain_id = ctx_chain_id
                parent_path = ctx_parent_path
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

        # R15.16 rule 3: per-issuing-window count cap. The issuing window is the
        # agent's wakeup for a wakeup-originated instruct, or the workflow run
        # for a workflow-originated one (which has no wakeup) — the run start is
        # the window boundary so a loop body issuing each iteration is bounded.
        issuing_window_start = wakeup_started_at
        if issuing_window_start is None and workflow_run_id is not None:
            issuing_window_start = await self._run_started_at(workflow_run_id)
        if issuing_window_start:
            count = await self._instructions.count_issued_by_agent_since(
                issuer_agent_id,
                issuing_window_start,
            )
            if count >= max_per_wakeup:
                await audit.emit(
                    self._db,
                    audit.AuditEvent(
                        action="instruct.budget_exceeded",
                        resource_type="agent",
                        resource_id=issuer_agent_id,
                        metadata={
                            "issuer_agent_id": str(issuer_agent_id),
                            "count": count,
                            "max_per_wakeup": max_per_wakeup,
                            "workflow_run_id": str(workflow_run_id) if workflow_run_id else None,
                        },
                    ),
                )
                raise InstructBudgetExceeded(
                    f"agent {issuer_agent_id} issued {count} instructs "
                    f"this issuing window (max {max_per_wakeup})"
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
            workflow_run_id=workflow_run_id,
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
        # Send BEFORE emitting instruct.issued. A denied send (A2AForbidden) or
        # any delivery failure must not leave an orphan `issued` row: the executor
        # swallows the exception into a `failure` port without rolling back, so
        # retention (which reaps only all-terminal chains) would never collect it.
        # Compensate by deleting the just-inserted row, then re-raise.
        try:
            await self._a2a.send(envelope=envelope)
        except Exception:
            await self._instructions.delete(instruction_id)
            raise

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

    async def _run_started_at(self, workflow_run_id: uuid.UUID) -> datetime | None:
        """Run start = the [R15.16] rule-3 issuing window for a workflow instruct.

        Lazy import: the workflow context reaches OrchestrationFacade -> here, so
        importing WorkflowFacade at module load risks a cycle (same reason as
        ``a2a_service._enforce_workflow_tenant``).
        """
        from contexts.workflow.interfaces.facade import WorkflowFacade

        run = await WorkflowFacade(self._db).get_run(workflow_run_id)
        return run.started_at if run else None

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

    async def resolve_instruction_project(self, instruction_id: uuid.UUID) -> uuid.UUID | None:
        """Project owning an instruction (via its issuer agent) — authz helper (API-2).

        A2A scope enforcement blocks cross-project instruct, so the issuer and
        target always share a project; resolving via the issuer is sufficient.
        Returns None when the instruction or its agent no longer exists.
        """
        instruction = await self._instructions.get(instruction_id)
        if instruction is None:
            return None
        agent = await self._agents.get_agent(instruction.issuer_agent_id, include_deleted=True)
        return agent.project_id if agent else None

    async def resolve_chain_project(self, chain_id: uuid.UUID) -> uuid.UUID | None:
        """Project owning an instruction chain (via any instruction's issuer).

        Every instruction in a chain shares one project (cross-project instruct
        is blocked at delivery), so the first row's issuer is representative.
        Returns None when the chain is empty or its agent no longer exists.
        """
        instructions = await self._instructions.list_for_chain(chain_id)
        if not instructions:
            return None
        agent = await self._agents.get_agent(instructions[0].issuer_agent_id, include_deleted=True)
        return agent.project_id if agent else None


__all__ = ["InstructService"]
