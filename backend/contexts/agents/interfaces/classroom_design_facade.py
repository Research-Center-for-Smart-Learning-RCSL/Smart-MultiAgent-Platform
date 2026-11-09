"""Classroom design runs ([R34.08]) -- public surface for the API and the worker.

Kept apart from ``AgentsFacade``: a run is its own lifecycle (submit, draft,
apply) over two agents, with a worker-only write surface.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.classroom_design import (
    DESIGN_UNAVAILABLE,
    MAX_FORM_NOTES_CHARS,
    MAX_ROUNDS,
    MIN_ROUNDS,
    ClassroomDesignRun,
    DesignActivity,
    DesignForm,
    DesignProposal,
    DesignRunStatus,
    Difficulty,
    PeerErrorPattern,
    Tone,
)
from contexts.agents.domain.models import Agent

__all__ = [
    "DESIGN_UNAVAILABLE",
    "MAX_FORM_NOTES_CHARS",
    "MAX_ROUNDS",
    "MIN_ROUNDS",
    "ClassroomDesignFacade",
    "ClassroomDesignRun",
    "DesignActivity",
    "DesignForm",
    "DesignProposal",
    "DesignRunStatus",
    "Difficulty",
    "PeerErrorPattern",
    "Tone",
]


class ClassroomDesignFacade:
    def __init__(self, db: AsyncSession) -> None:
        # Lazy: the service reaches AgentService, which reaches the turn runtime,
        # which imports the agents facade; resolving it here keeps import order free.
        from contexts.agents.application.classroom_design_service import ClassroomDesignService

        self._svc = ClassroomDesignService(db)

    async def create_run(
        self, *, project_id: uuid.UUID, form: DesignForm, actor_user_id: uuid.UUID
    ) -> ClassroomDesignRun:
        """Never commits; the caller enqueues ``run_classroom_design`` after it does."""
        return await self._svc.create_run(project_id=project_id, form=form, actor_user_id=actor_user_id)

    async def get_run(self, *, project_id: uuid.UUID, run_id: uuid.UUID) -> ClassroomDesignRun:
        return await self._svc.get_run(project_id=project_id, run_id=run_id)

    async def list_runs(self, project_id: uuid.UUID, *, limit: int) -> Sequence[ClassroomDesignRun]:
        return await self._svc.list_runs(project_id, limit=limit)

    async def apply(
        self,
        *,
        project_id: uuid.UUID,
        run_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> tuple[Agent, Agent]:
        """Both target agents after the write. Never commits; roll back on error."""
        return await self._svc.apply(
            project_id=project_id,
            run_id=run_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    # -- Worker surface -------------------------------------------------------

    async def begin(self, run_id: uuid.UUID) -> ClassroomDesignRun | None:
        return await self._svc.begin(run_id)

    async def record_success(self, run_id: uuid.UUID, proposal: DesignProposal) -> bool:
        return await self._svc.record_success(run_id, proposal)

    async def record_failure(self, run_id: uuid.UUID, reason: str) -> bool:
        return await self._svc.record_failure(run_id, reason)
