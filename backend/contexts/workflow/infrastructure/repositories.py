"""Workflow repositories — SQL queries, row→domain translation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.infrastructure.tables import workspaces
from contexts.orchestration.infrastructure.tables import workflow_runs
from contexts.workflow.domain.models import (
    RunState,
    StepState,
    Workflow,
    WorkflowRun,
    WorkflowStep,
)
from contexts.workflow.infrastructure.tables import (
    workflow_runs_archive,
    workflow_steps,
    workflows,
)

# ---------------------------------------------------------------------------
# Row → domain helpers
# ---------------------------------------------------------------------------


def _row_to_workflow(row: Any) -> Workflow:
    return Workflow(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        definition=row.definition,
        version=row.version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _row_to_run(row: Any) -> WorkflowRun:
    return WorkflowRun(
        id=row.id,
        workflow_id=row.workflow_id,
        trigger_type=row.trigger_type or "",
        started_by_user_id=row.started_by_user_id,
        state=RunState(row.state),
        variables=row.variables or {},
        context=row.context or {},
        started_at=row.started_at,
        ended_at=row.ended_at,
    )


def _row_to_step(row: Any) -> WorkflowStep:
    return WorkflowStep(
        id=row.id,
        run_id=row.run_id,
        node_id=row.node_id,
        state=StepState(row.state),
        started_at=row.started_at,
        ended_at=row.ended_at,
        input=row.input or {},
        output=row.output or {},
        error=row.error,
    )


# ---------------------------------------------------------------------------
# WorkflowRepository
# ---------------------------------------------------------------------------


class WorkflowRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(
        self,
        workflow_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Workflow | None:
        q = sa.select(workflows).where(workflows.c.id == workflow_id)
        if not include_deleted:
            q = q.where(workflows.c.deleted_at.is_(None))
        row = (await self._db.execute(q)).first()
        return _row_to_workflow(row) if row else None

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Workflow]:
        q = (
            sa.select(workflows)
            .where(workflows.c.workspace_id == workspace_id)
            .order_by(workflows.c.created_at.desc())
        )
        if not include_deleted:
            q = q.where(workflows.c.deleted_at.is_(None))
        q = q.limit(limit).offset(offset)
        rows = (await self._db.execute(q)).all()
        return [_row_to_workflow(r) for r in rows]

    async def insert(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        definition: dict[str, Any],
    ) -> Workflow:
        row = (
            await self._db.execute(
                workflows.insert()
                .values(
                    workspace_id=workspace_id,
                    name=name,
                    definition=definition,
                )
                .returning(workflows),
            )
        ).first()
        assert row is not None
        return _row_to_workflow(row)

    async def update(
        self,
        workflow_id: uuid.UUID,
        *,
        expected_version: int,
        name: str | None = None,
        definition: dict[str, Any] | None = None,
    ) -> Workflow | None:
        # `version` is bumped by the smap_bump_version trigger (migration
        # 0029); never increment it here.
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if definition is not None:
            values["definition"] = definition
        if not values:
            # Empty patch: nothing to write. Verify the row still exists at
            # the expected version and return it unchanged — the trigger only
            # fires on a real UPDATE, so an empty SET would be invalid SQL.
            current = await self.get(workflow_id)
            if current is None or current.version != expected_version:
                return None
            return current

        row = (
            await self._db.execute(
                workflows.update()
                .where(
                    sa.and_(
                        workflows.c.id == workflow_id,
                        workflows.c.version == expected_version,
                        workflows.c.deleted_at.is_(None),
                    ),
                )
                .values(**values)
                .returning(workflows),
            )
        ).first()
        return _row_to_workflow(row) if row else None

    async def resolve_project_id(self, workflow_id: uuid.UUID) -> uuid.UUID | None:
        """Resolve the owning ``project_id`` via ``workflows → workspaces``.

        ``workflow_runs.project_id`` is a NOT-NULL FK to ``projects``; a run
        triggered without an explicit project (the cron scheduler) must derive
        it here rather than fall back to a bogus ``UUID(int=0)`` that violates
        the FK on insert. Returns ``None`` if the workflow (or its workspace) is
        gone, in which case the caller declines to start the run.
        """
        row = (
            await self._db.execute(
                sa.select(workspaces.c.project_id)
                .select_from(workflows.join(workspaces, workflows.c.workspace_id == workspaces.c.id))
                .where(workflows.c.id == workflow_id)
            )
        ).first()
        return row[0] if row else None

    async def soft_delete(self, workflow_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            workflows.update()
            .where(
                sa.and_(
                    workflows.c.id == workflow_id,
                    workflows.c.deleted_at.is_(None),
                ),
            )
            .values(deleted_at=sa.text("now()")),
        )
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# WorkflowRunRepository
# ---------------------------------------------------------------------------


class WorkflowRunRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, run_id: uuid.UUID) -> WorkflowRun | None:
        row = (
            await self._db.execute(
                sa.select(workflow_runs).where(workflow_runs.c.id == run_id),
            )
        ).first()
        return _row_to_run(row) if row else None

    async def get_project_id(self, run_id: uuid.UUID) -> uuid.UUID | None:
        """Return the run's `project_id`, or None if the run does not exist.

        Used by the API layer to scope authorization without loading the full
        run — the domain `WorkflowRun` deliberately omits `project_id`.
        """
        row = (
            await self._db.execute(
                sa.select(workflow_runs.c.project_id).where(workflow_runs.c.id == run_id),
            )
        ).first()
        return row[0] if row else None

    async def list_for_workflow(
        self,
        workflow_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        rows = (
            await self._db.execute(
                sa.select(workflow_runs)
                .where(workflow_runs.c.workflow_id == workflow_id)
                .order_by(workflow_runs.c.started_at.desc())
                .limit(limit)
                .offset(offset),
            )
        ).all()
        return [_row_to_run(r) for r in rows]

    async def insert(
        self,
        *,
        project_id: uuid.UUID,
        workflow_id: uuid.UUID,
        trigger_type: str,
        started_by_user_id: uuid.UUID | None = None,
        variables: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        row = (
            await self._db.execute(
                workflow_runs.insert()
                .values(
                    project_id=project_id,
                    workflow_id=workflow_id,
                    trigger_type=trigger_type,
                    started_by_user_id=started_by_user_id,
                    state="running",
                    variables=variables or {},
                    context=context or {},
                )
                .returning(workflow_runs),
            )
        ).first()
        assert row is not None
        return _row_to_run(row)

    # H13: only legal state transitions are allowed; terminal states cannot be
    # overwritten.  The WHERE clause enforces this at the DB level.
    _VALID_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        "running": {"waiting", "succeeded", "failed", "cancelled"},
        "waiting": {"running", "failed", "cancelled"},
    }

    async def update_state(
        self,
        run_id: uuid.UUID,
        *,
        state: RunState,
        ended_at: datetime | None = None,
        variables: dict[str, Any] | None = None,
    ) -> bool:
        allowed_from = [k for k, v in self._VALID_TRANSITIONS.items() if state.value in v]
        if not allowed_from:
            return False
        values: dict[str, Any] = {"state": state.value}
        if ended_at is not None:
            values["ended_at"] = ended_at
        if variables is not None:
            values["variables"] = variables
        result = await self._db.execute(
            workflow_runs.update()
            .where(
                sa.and_(
                    workflow_runs.c.id == run_id,
                    workflow_runs.c.state.in_(allowed_from),
                ),
            )
            .values(**values),
        )
        return result.rowcount > 0

    async def update_variables(
        self,
        run_id: uuid.UUID,
        variables: dict[str, Any],
    ) -> None:
        await self._db.execute(
            workflow_runs.update().where(workflow_runs.c.id == run_id).values(variables=variables),
        )

    async def list_active(self) -> list[tuple[uuid.UUID, uuid.UUID, datetime]]:
        """``(run_id, workflow_id, started_at)`` for every RUNNING/WAITING run.

        Drives the timeout watchdog (K.4) — it loads each run's workflow
        definition to read ``run_max_seconds`` / ``idle_max_seconds``.
        """
        rows = (
            await self._db.execute(
                sa.select(
                    workflow_runs.c.id,
                    workflow_runs.c.workflow_id,
                    workflow_runs.c.started_at,
                ).where(workflow_runs.c.state.in_(["running", "waiting"]))
            )
        ).all()
        return [(r.id, r.workflow_id, r.started_at) for r in rows]


# ---------------------------------------------------------------------------
# WorkflowStepRepository
# ---------------------------------------------------------------------------


class WorkflowStepRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        run_id: uuid.UUID,
        node_id: str,
        state: StepState = StepState.PENDING,
        input_data: dict[str, Any] | None = None,
    ) -> WorkflowStep:
        row = (
            await self._db.execute(
                workflow_steps.insert()
                .values(
                    run_id=run_id,
                    node_id=node_id,
                    state=state.value,
                    input=input_data or {},
                )
                .returning(workflow_steps),
            )
        ).first()
        assert row is not None
        return _row_to_step(row)

    async def update(
        self,
        step_id: uuid.UUID,
        *,
        state: StepState | None = None,
        ended_at: datetime | None = None,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        values: dict[str, Any] = {}
        if state is not None:
            values["state"] = state.value
        if ended_at is not None:
            values["ended_at"] = ended_at
        if output is not None:
            values["output"] = output
        if error is not None:
            values["error"] = error
        if not values:
            return False
        result = await self._db.execute(
            workflow_steps.update().where(workflow_steps.c.id == step_id).values(**values),
        )
        return result.rowcount > 0

    async def list_for_run(self, run_id: uuid.UUID) -> list[WorkflowStep]:
        rows = (
            await self._db.execute(
                sa.select(workflow_steps)
                .where(workflow_steps.c.run_id == run_id)
                .order_by(workflow_steps.c.started_at),
            )
        ).all()
        return [_row_to_step(r) for r in rows]

    async def latest_activity_at(self, run_id: uuid.UUID) -> datetime | None:
        """Most recent step ``started_at`` for a run — the idle-watchdog clock."""
        row = (
            await self._db.execute(
                sa.select(sa.func.max(workflow_steps.c.started_at)).where(workflow_steps.c.run_id == run_id)
            )
        ).first()
        return row[0] if row else None

    async def cancel_pending_for_run(self, run_id: uuid.UUID) -> int:
        result = await self._db.execute(
            workflow_steps.update()
            .where(
                sa.and_(
                    workflow_steps.c.run_id == run_id,
                    workflow_steps.c.state.in_(["pending", "running"]),
                ),
            )
            .values(state="cancelled", ended_at=sa.text("now()")),
        )
        return result.rowcount


# ---------------------------------------------------------------------------
# ArchiveRepository (H.6 — query only, archival worker is Phase H.6)
# ---------------------------------------------------------------------------


class WorkflowRunArchiveRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_workflow(
        self,
        workflow_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = (
            await self._db.execute(
                sa.select(workflow_runs_archive)
                .where(workflow_runs_archive.c.workflow_id == workflow_id)
                .order_by(workflow_runs_archive.c.started_at.desc())
                .limit(limit)
                .offset(offset),
            )
        ).all()
        return [dict(row._mapping) for row in rows]

    async def list_union_for_workflow(
        self,
        workflow_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Union current runs + archived runs, ordered by started_at desc."""
        live = sa.select(
            workflow_runs.c.id,
            workflow_runs.c.workflow_id,
            workflow_runs.c.trigger_type,
            workflow_runs.c.started_by_user_id,
            workflow_runs.c.state,
            workflow_runs.c.started_at,
            workflow_runs.c.ended_at,
            sa.literal(False).label("archived"),
        ).where(workflow_runs.c.workflow_id == workflow_id)
        archived = sa.select(
            workflow_runs_archive.c.id,
            workflow_runs_archive.c.workflow_id,
            workflow_runs_archive.c.trigger_type,
            workflow_runs_archive.c.started_by_user_id,
            workflow_runs_archive.c.state,
            workflow_runs_archive.c.started_at,
            workflow_runs_archive.c.ended_at,
            sa.literal(True).label("archived"),
        ).where(workflow_runs_archive.c.workflow_id == workflow_id)
        union_q = sa.union_all(live, archived).subquery()
        rows = (
            await self._db.execute(
                sa.select(union_q).order_by(union_q.c.started_at.desc()).limit(limit).offset(offset),
            )
        ).all()
        return [dict(row._mapping) for row in rows]


__all__ = [
    "WorkflowRepository",
    "WorkflowRunArchiveRepository",
    "WorkflowRunRepository",
    "WorkflowStepRepository",
]
