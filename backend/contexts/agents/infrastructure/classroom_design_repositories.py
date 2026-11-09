"""Persistence for prompt revisions and classroom design runs ([R34.07], [R34.08]).

Every status change of a run is a compare-and-set on the current status, so a
redelivered job, a stale sweep and an apply racing each other each win at most
once. Repositories never commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.classroom_design import (
    DESIGN_STALE,
    IN_FLIGHT_STATUSES,
    ClassroomDesignRun,
    DesignProposal,
    DesignRunStatus,
    PromptRevision,
    PromptRevisionSource,
)
from contexts.agents.domain.errors import DesignRunInFlight
from contexts.agents.infrastructure import tables as t

_IN_FLIGHT_INDEX = "uq_classroom_design_runs_in_flight"
_IN_FLIGHT_VALUES = sorted(s.value for s in IN_FLIGHT_STATUSES)


def _row_to_revision(row: Any) -> PromptRevision:
    return PromptRevision(
        id=row.id,
        agent_id=row.agent_id,
        agent_version=row.agent_version,
        system_prompt=row.system_prompt,
        source=PromptRevisionSource(row.source),
        actor_user_id=row.actor_user_id,
        design_run_id=row.design_run_id,
        restored_from_id=row.restored_from_id,
        created_at=row.created_at,
    )


def _row_to_run(row: Any) -> ClassroomDesignRun:
    return ClassroomDesignRun(
        id=row.id,
        project_id=row.project_id,
        requested_by_user_id=row.requested_by_user_id,
        design_agent_id=row.design_agent_id,
        teacher_agent_id=row.teacher_agent_id,
        peer_agent_id=row.peer_agent_id,
        params=dict(row.params or {}),
        teacher_prompt_before=row.teacher_prompt_before,
        teacher_version_before=row.teacher_version_before,
        peer_prompt_before=row.peer_prompt_before,
        peer_version_before=row.peer_version_before,
        status=DesignRunStatus(row.status),
        proposed_teacher_prompt=row.proposed_teacher_prompt,
        proposed_peer_prompt=row.proposed_peer_prompt,
        notes=row.notes,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
        finished_at=row.finished_at,
        applied_at=row.applied_at,
        applied_by_user_id=row.applied_by_user_id,
    )


class PromptRevisionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def has_any(self, agent_id: uuid.UUID) -> bool:
        stmt = sa.select(sa.literal(1)).where(t.agent_prompt_revisions.c.agent_id == agent_id).limit(1)
        return (await self._db.execute(stmt)).first() is not None

    async def add(
        self,
        *,
        agent_id: uuid.UUID,
        agent_version: int,
        system_prompt: str,
        source: PromptRevisionSource,
        actor_user_id: uuid.UUID | None,
        design_run_id: uuid.UUID | None = None,
        restored_from_id: uuid.UUID | None = None,
    ) -> PromptRevision:
        stmt = (
            t.agent_prompt_revisions.insert()
            .values(
                agent_id=agent_id,
                agent_version=agent_version,
                system_prompt=system_prompt,
                source=source.value,
                actor_user_id=actor_user_id,
                design_run_id=design_run_id,
                restored_from_id=restored_from_id,
            )
            .returning(t.agent_prompt_revisions)
        )
        return _row_to_revision((await self._db.execute(stmt)).one())

    async def list(self, agent_id: uuid.UUID, *, limit: int, offset: int) -> Sequence[PromptRevision]:
        stmt = (
            sa.select(t.agent_prompt_revisions)
            .where(t.agent_prompt_revisions.c.agent_id == agent_id)
            .order_by(t.agent_prompt_revisions.c.agent_version.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_row_to_revision(r) for r in (await self._db.execute(stmt)).all()]

    async def get(self, *, agent_id: uuid.UUID, revision_id: uuid.UUID) -> PromptRevision | None:
        stmt = sa.select(t.agent_prompt_revisions).where(
            t.agent_prompt_revisions.c.id == revision_id,
            t.agent_prompt_revisions.c.agent_id == agent_id,
        )
        row = (await self._db.execute(stmt)).first()
        return _row_to_revision(row) if row is not None else None


class DesignRunRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def expire_stale(self, *, project_id: uuid.UUID, older_than_seconds: int) -> int:
        """Fail the project's in-flight runs older than the threshold; the count."""
        runs = t.classroom_design_runs
        stmt = (
            runs.update()
            .where(
                runs.c.project_id == project_id,
                runs.c.status.in_(_IN_FLIGHT_VALUES),
                runs.c.created_at
                < sa.func.now() - sa.literal(timedelta(seconds=older_than_seconds), type_=sa.Interval),
            )
            .values(
                status=DesignRunStatus.FAILED.value,
                failure_reason=DESIGN_STALE,
                finished_at=sa.func.now(),
            )
            .returning(runs.c.id)
        )
        return len((await self._db.execute(stmt)).all())

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        requested_by_user_id: uuid.UUID,
        design_agent_id: uuid.UUID,
        teacher_agent_id: uuid.UUID,
        peer_agent_id: uuid.UUID,
        params: dict[str, Any],
        teacher_prompt_before: str,
        teacher_version_before: int,
        peer_prompt_before: str,
        peer_version_before: int,
    ) -> ClassroomDesignRun:
        stmt = (
            t.classroom_design_runs.insert()
            .values(
                project_id=project_id,
                requested_by_user_id=requested_by_user_id,
                design_agent_id=design_agent_id,
                teacher_agent_id=teacher_agent_id,
                peer_agent_id=peer_agent_id,
                params=params,
                teacher_prompt_before=teacher_prompt_before,
                teacher_version_before=teacher_version_before,
                peer_prompt_before=peer_prompt_before,
                peer_version_before=peer_version_before,
            )
            .returning(t.classroom_design_runs)
        )
        # A savepoint, so losing the one-in-flight race leaves the caller's
        # transaction usable for the error response's audit and rollback.
        try:
            async with self._db.begin_nested():
                row = (await self._db.execute(stmt)).one()
        except IntegrityError as exc:
            if _IN_FLIGHT_INDEX in str(exc.orig or exc):
                raise DesignRunInFlight(str(project_id)) from exc
            raise
        return _row_to_run(row)

    async def get(self, run_id: uuid.UUID) -> ClassroomDesignRun | None:
        row = (
            await self._db.execute(
                sa.select(t.classroom_design_runs).where(t.classroom_design_runs.c.id == run_id)
            )
        ).first()
        return _row_to_run(row) if row is not None else None

    async def list_for_project(self, project_id: uuid.UUID, *, limit: int) -> Sequence[ClassroomDesignRun]:
        runs = t.classroom_design_runs
        stmt = (
            sa.select(runs)
            .where(runs.c.project_id == project_id)
            .order_by(runs.c.created_at.desc(), runs.c.id.desc())
            .limit(limit)
        )
        return [_row_to_run(r) for r in (await self._db.execute(stmt)).all()]

    async def _transition(
        self, run_id: uuid.UUID, *, from_statuses: Sequence[DesignRunStatus], values: dict[str, Any]
    ) -> bool:
        runs = t.classroom_design_runs
        stmt = (
            runs.update()
            .where(runs.c.id == run_id, runs.c.status.in_([s.value for s in from_statuses]))
            .values(**values)
            .returning(runs.c.id)
        )
        return (await self._db.execute(stmt)).first() is not None

    async def mark_running(self, run_id: uuid.UUID) -> bool:
        return await self._transition(
            run_id,
            from_statuses=[DesignRunStatus.PENDING],
            values={"status": DesignRunStatus.RUNNING.value},
        )

    async def record_success(self, run_id: uuid.UUID, proposal: DesignProposal) -> bool:
        return await self._transition(
            run_id,
            from_statuses=[DesignRunStatus.RUNNING],
            values={
                "status": DesignRunStatus.SUCCEEDED.value,
                "proposed_teacher_prompt": proposal.teacher_system_prompt,
                "proposed_peer_prompt": proposal.peer_system_prompt,
                "notes": proposal.notes,
                "finished_at": sa.func.now(),
            },
        )

    async def record_failure(self, run_id: uuid.UUID, reason: str) -> bool:
        return await self._transition(
            run_id,
            from_statuses=[DesignRunStatus.PENDING, DesignRunStatus.RUNNING],
            values={
                "status": DesignRunStatus.FAILED.value,
                "failure_reason": reason,
                "finished_at": sa.func.now(),
            },
        )

    async def mark_applied(self, run_id: uuid.UUID, *, applied_by_user_id: uuid.UUID) -> bool:
        return await self._transition(
            run_id,
            from_statuses=[DesignRunStatus.SUCCEEDED],
            values={
                "status": DesignRunStatus.APPLIED.value,
                "applied_at": sa.func.now(),
                "applied_by_user_id": applied_by_user_id,
            },
        )


__all__ = ["DesignRunRepository", "PromptRevisionRepository"]
