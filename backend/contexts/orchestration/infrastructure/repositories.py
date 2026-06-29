"""Orchestration repositories (G.6–G.8).

Thin DB access layer — no domain logic. Services compose these
with domain rules and audit writes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.orchestration.domain.models import (
    AgentInstance,
    Approval,
    ApprovalMode,
    ApprovalState,
    ApprovalVote,
    Instruction,
    InstructionState,
)
from contexts.orchestration.infrastructure.tables import (
    agent_instances,
    approval_votes,
    approvals,
    instructions,
    workflow_runs,
)


async def _project_id_for_run(
    db: AsyncSession,
    workflow_run_id: uuid.UUID,
) -> uuid.UUID | None:
    """Resolve a workflow run to its owning project_id, or None if absent.

    Shared by the approval and agent-instance repositories for the API-2
    authz scope check; keeping it in one place stops the two AuthZ predicates
    from silently diverging on a schema change.
    """
    row = (
        await db.execute(
            sa.select(workflow_runs.c.project_id).where(workflow_runs.c.id == workflow_run_id),
        )
    ).first()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Approval repository
# ---------------------------------------------------------------------------


class ApprovalRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        id: uuid.UUID,  # noqa: A002 — mirrors the `id` column name
        workflow_run_id: uuid.UUID,
        mode: ApprovalMode,
        leader_agent_id: uuid.UUID,
        approver_agent_ids: list[uuid.UUID],
        timeout_seconds: int,
    ) -> Approval:
        now = datetime.now(UTC)
        await self._db.execute(
            approvals.insert().values(
                id=id,
                workflow_run_id=workflow_run_id,
                mode=mode.value,
                leader_agent_id=leader_agent_id,
                approver_agent_ids=approver_agent_ids,
                timeout_seconds=timeout_seconds,
                state=ApprovalState.PENDING.value,
                started_at=now,
            ),
        )
        return Approval(
            id=id,
            workflow_run_id=workflow_run_id,
            mode=mode,
            leader_agent_id=leader_agent_id,
            approver_agent_ids=tuple(approver_agent_ids),
            timeout_seconds=timeout_seconds,
            state=ApprovalState.PENDING,
            started_at=now,
            ended_at=None,
        )

    async def get(self, approval_id: uuid.UUID) -> Approval | None:
        row = (
            (
                await self._db.execute(
                    approvals.select().where(approvals.c.id == approval_id),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return _row_to_approval(row)

    async def update_state(
        self,
        approval_id: uuid.UUID,
        state: ApprovalState,
    ) -> bool:
        """Compare-and-set: ``pending`` → ``state``.

        Returns True iff this call won the transition (the row was still
        pending). Vote-driven resolution and the timeout job race in separate
        sessions; the WHERE guard makes resolution single-shot so a late
        timeout can never overwrite a committed APPROVED/REJECTED (and two
        concurrent votes cannot both resolve+publish).
        """
        ended = datetime.now(UTC) if state != ApprovalState.PENDING else None
        result = await self._db.execute(
            approvals.update()
            .where(
                approvals.c.id == approval_id,
                approvals.c.state == ApprovalState.PENDING.value,
            )
            .values(state=state.value, ended_at=ended),
        )
        return (result.rowcount or 0) > 0

    async def get_project_id(self, approval_id: uuid.UUID) -> uuid.UUID | None:
        """Resolve approval → workflow_run → project for the authz scope check.

        Returns None when the approval does not exist (API-2).
        """
        row = (
            await self._db.execute(
                sa.select(workflow_runs.c.project_id)
                .select_from(
                    approvals.join(
                        workflow_runs,
                        approvals.c.workflow_run_id == workflow_runs.c.id,
                    ),
                )
                .where(approvals.c.id == approval_id),
            )
        ).first()
        return row[0] if row else None

    async def project_for_run(self, workflow_run_id: uuid.UUID) -> uuid.UUID | None:
        """Return the project_id owning a workflow run, or None if absent (API-2)."""
        return await _project_id_for_run(self._db, workflow_run_id)

    async def list_for_workflow_run(
        self,
        workflow_run_id: uuid.UUID,
    ) -> list[Approval]:
        rows = (
            (
                await self._db.execute(
                    approvals.select()
                    .where(approvals.c.workflow_run_id == workflow_run_id)
                    .order_by(approvals.c.started_at),
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_approval(r) for r in rows]


def _row_to_approval(row: Any) -> Approval:
    return Approval(
        id=row["id"],
        workflow_run_id=row["workflow_run_id"],
        mode=ApprovalMode(row["mode"]),
        leader_agent_id=row["leader_agent_id"],
        approver_agent_ids=tuple(row["approver_agent_ids"]),
        timeout_seconds=row["timeout_seconds"],
        state=ApprovalState(row["state"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )


# ---------------------------------------------------------------------------
# Approval votes repository
# ---------------------------------------------------------------------------


class ApprovalVoteRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def cast(
        self,
        *,
        approval_id: uuid.UUID,
        voter_agent_id: uuid.UUID,
        vote: bool,
        rationale: str | None = None,
    ) -> ApprovalVote:
        now = datetime.now(UTC)
        await self._db.execute(
            pg.insert(approval_votes)
            .values(
                approval_id=approval_id,
                voter_agent_id=voter_agent_id,
                vote=vote,
                rationale=rationale,
                cast_at=now,
            )
            .on_conflict_do_update(
                constraint="pk_approval_votes",
                set_={"vote": vote, "rationale": rationale, "cast_at": now},
            ),
        )
        return ApprovalVote(
            approval_id=approval_id,
            voter_agent_id=voter_agent_id,
            vote=vote,
            rationale=rationale,
            cast_at=now,
        )

    async def list_for_approval(
        self,
        approval_id: uuid.UUID,
    ) -> list[ApprovalVote]:
        rows = (
            (
                await self._db.execute(
                    approval_votes.select()
                    .where(approval_votes.c.approval_id == approval_id)
                    .order_by(approval_votes.c.cast_at),
                )
            )
            .mappings()
            .all()
        )
        return [
            ApprovalVote(
                approval_id=r["approval_id"],
                voter_agent_id=r["voter_agent_id"],
                vote=r["vote"],
                rationale=r["rationale"],
                cast_at=r["cast_at"],
            )
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Instruction repository
# ---------------------------------------------------------------------------


class InstructionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        id: uuid.UUID,  # noqa: A002 — mirrors the `id` column name
        chain_id: uuid.UUID,
        path: list[uuid.UUID],
        depth: int,
        issuer_agent_id: uuid.UUID,
        target_agent_id: uuid.UUID,
        payload: dict[str, Any],
        state: InstructionState = InstructionState.ISSUED,
    ) -> Instruction:
        now = datetime.now(UTC)
        await self._db.execute(
            instructions.insert().values(
                id=id,
                chain_id=chain_id,
                path=path,
                depth=depth,
                issuer_agent_id=issuer_agent_id,
                target_agent_id=target_agent_id,
                payload=payload,
                state=state.value,
                issued_at=now,
            ),
        )
        return Instruction(
            id=id,
            chain_id=chain_id,
            path=tuple(path),
            depth=depth,
            issuer_agent_id=issuer_agent_id,
            target_agent_id=target_agent_id,
            payload=payload,
            state=state,
            issued_at=now,
            resolved_at=None,
        )

    async def get(self, instruction_id: uuid.UUID) -> Instruction | None:
        row = (
            (
                await self._db.execute(
                    instructions.select().where(instructions.c.id == instruction_id),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return _row_to_instruction(row)

    async def update_state(
        self,
        instruction_id: uuid.UUID,
        state: InstructionState,
    ) -> None:
        resolved = (
            datetime.now(UTC) if state not in (InstructionState.ISSUED, InstructionState.DELIVERED) else None
        )
        result = await self._db.execute(
            instructions.update()
            .where(instructions.c.id == instruction_id)
            .values(state=state.value, resolved_at=resolved),
        )
        if (result.rowcount or 0) == 0:
            raise ValueError(f"instruction {instruction_id} not found")

    async def count_issued_by_agent_since(
        self,
        agent_id: uuid.UUID,
        since: datetime,
    ) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(instructions)
            .where(
                instructions.c.issuer_agent_id == agent_id,
                instructions.c.issued_at >= since,
            ),
        )
        return int(result.scalar_one())

    async def list_for_chain(self, chain_id: uuid.UUID) -> list[Instruction]:
        rows = (
            (
                await self._db.execute(
                    instructions.select()
                    .where(instructions.c.chain_id == chain_id)
                    .order_by(instructions.c.issued_at),
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_instruction(r) for r in rows]

    async def get_chain_start_time(self, chain_id: uuid.UUID) -> datetime | None:
        result = await self._db.execute(
            sa.select(sa.func.min(instructions.c.issued_at)).where(instructions.c.chain_id == chain_id),
        )
        return result.scalar_one()  # type: ignore[no-any-return]


def _row_to_instruction(row: Any) -> Instruction:
    return Instruction(
        id=row["id"],
        chain_id=row["chain_id"],
        path=tuple(row["path"]),
        depth=row["depth"],
        issuer_agent_id=row["issuer_agent_id"],
        target_agent_id=row["target_agent_id"],
        payload=row["payload"],
        state=InstructionState(row["state"]),
        issued_at=row["issued_at"],
        resolved_at=row["resolved_at"],
    )


# ---------------------------------------------------------------------------
# Agent instance repository
# ---------------------------------------------------------------------------


class AgentInstanceRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        id: uuid.UUID,  # noqa: A002 — mirrors the `id` column name
        agent_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        chatroom_id: uuid.UUID | None,
        run_context: dict[str, Any],
        task_description: str | None = None,
    ) -> AgentInstance:
        now = datetime.now(UTC)
        await self._db.execute(
            agent_instances.insert().values(
                id=id,
                agent_id=agent_id,
                parent_id=parent_id,
                chatroom_id=chatroom_id,
                run_context=run_context,
                task_description=task_description,
                state="running",
                spawned_at=now,
            ),
        )
        return AgentInstance(
            id=id,
            agent_id=agent_id,
            parent_id=parent_id,
            chatroom_id=chatroom_id,
            run_context=run_context,
            task_description=task_description,
            state="running",
            spawned_at=now,
            destroyed_at=None,
        )

    async def get(self, instance_id: uuid.UUID) -> AgentInstance | None:
        row = (
            (
                await self._db.execute(
                    agent_instances.select().where(agent_instances.c.id == instance_id),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return _row_to_instance(row)

    async def find_alive_root_for_workflow_run(
        self,
        *,
        agent_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
    ) -> AgentInstance | None:
        """Return the live synthetic root instance for a workflow run, if any.

        A workflow ``subagent_spawn`` node has no real parent agent instance,
        so ``SubagentService`` creates one depth-0 root instance (``parent_id``
        NULL) per (agent, workflow run) and reuses it for every subagent of
        that run. The run id is stamped into ``run_context``.
        """
        row = (
            (
                await self._db.execute(
                    agent_instances.select()
                    .where(
                        agent_instances.c.agent_id == agent_id,
                        agent_instances.c.parent_id.is_(None),
                        agent_instances.c.destroyed_at.is_(None),
                        agent_instances.c.run_context["workflow_run_id"].astext == str(workflow_run_id),
                    )
                    .limit(1),
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return _row_to_instance(row)

    async def list_for_workflow_run(
        self,
        workflow_run_id: uuid.UUID,
    ) -> list[AgentInstance]:
        """Return every sub-agent spawned during a workflow run (alive + dead).

        Sub-agents are the depth-1 children of the synthetic depth-0 root(s)
        created per (agent, workflow run); the run id is stamped into each
        root's ``run_context``. The admin backstage trace inspects finished
        runs whose sub-agents have already been destroyed, so the alive-only
        :meth:`list_alive_children` would return nothing — hence this query
        does not filter on ``destroyed_at``.
        """
        roots = (
            sa.select(agent_instances.c.id)
            .where(
                agent_instances.c.parent_id.is_(None),
                agent_instances.c.run_context["workflow_run_id"].astext == str(workflow_run_id),
            )
            .scalar_subquery()
        )
        rows = (
            (
                await self._db.execute(
                    agent_instances.select()
                    .where(agent_instances.c.parent_id.in_(roots))
                    .order_by(agent_instances.c.spawned_at),
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_instance(r) for r in rows]

    async def project_for_workflow_run(
        self,
        workflow_run_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Return the project_id owning a workflow run, or None if absent (API-2)."""
        return await _project_id_for_run(self._db, workflow_run_id)

    async def destroy(self, instance_id: uuid.UUID, state: str = "completed") -> None:
        await self._db.execute(
            agent_instances.update()
            .where(agent_instances.c.id == instance_id)
            .values(state=state, destroyed_at=datetime.now(UTC)),
        )

    async def lock_parent(self, parent_id: uuid.UUID) -> None:
        """Serialise concurrent spawns for one parent (R15.20).

        A transaction-level advisory lock held until commit makes the
        count-alive-then-insert cap check in ``SubagentService.spawn`` atomic
        across concurrent sessions, so two parallel spawns cannot both read
        ``alive < cap`` and breach the hard concurrency cap. The UUID is folded
        into a signed 64-bit key for ``pg_advisory_xact_lock``.
        """
        key = int.from_bytes(parent_id.bytes[:8], "big", signed=True)
        await self._db.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})

    async def count_alive_children(self, parent_id: uuid.UUID) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(agent_instances)
            .where(
                agent_instances.c.parent_id == parent_id,
                agent_instances.c.destroyed_at.is_(None),
            ),
        )
        return int(result.scalar_one())

    async def list_alive_children(
        self,
        parent_id: uuid.UUID,
    ) -> list[AgentInstance]:
        rows = (
            (
                await self._db.execute(
                    agent_instances.select()
                    .where(
                        agent_instances.c.parent_id == parent_id,
                        agent_instances.c.destroyed_at.is_(None),
                    )
                    .order_by(agent_instances.c.spawned_at),
                )
            )
            .mappings()
            .all()
        )
        return [_row_to_instance(r) for r in rows]

    async def delete_older_than_days(self, days: int) -> int:
        cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = cutoff - timedelta(days=days)
        result = await self._db.execute(
            agent_instances.delete().where(
                agent_instances.c.destroyed_at.isnot(None),
                agent_instances.c.destroyed_at < cutoff,
            ),
        )
        return result.rowcount


def _row_to_instance(row: Any) -> AgentInstance:
    return AgentInstance(
        id=row["id"],
        agent_id=row["agent_id"],
        parent_id=row["parent_id"],
        chatroom_id=row["chatroom_id"],
        run_context=row["run_context"],
        task_description=row["task_description"],
        state=row["state"],
        spawned_at=row["spawned_at"],
        destroyed_at=row["destroyed_at"],
    )


__all__ = [
    "AgentInstanceRepository",
    "ApprovalRepository",
    "ApprovalVoteRepository",
    "InstructionRepository",
]
