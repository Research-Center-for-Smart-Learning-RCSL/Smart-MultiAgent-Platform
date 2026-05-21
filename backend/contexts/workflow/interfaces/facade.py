"""Workflow facade — public surface for other contexts.

Other contexts (Orchestration, Conversation) use this facade to:
- Validate workflow definitions
- Trigger workflow runs
- Query run state
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.orchestration.infrastructure.tables import workflow_runs as _workflow_runs_table
from contexts.workflow.application.run_engine import RunEngine
from contexts.workflow.application.workflow_service import WorkflowService
from contexts.workflow.domain.models import (
    ValidationResult,
    Workflow,
    WorkflowRun,
    WorkflowStep,
)


class WorkflowFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._svc = WorkflowService(db)
        self._engine = RunEngine(db)

    async def get_workflow(self, workflow_id: uuid.UUID) -> Workflow | None:
        try:
            return await self._svc.get(workflow_id)
        except Exception:
            return None

    def validate_definition(
        self,
        definition: dict[str, Any],
        *,
        valid_agent_ids: frozenset[str] = frozenset(),
        valid_chatroom_ids: frozenset[str] = frozenset(),
    ) -> ValidationResult:
        return self._svc.validate(
            definition,
            valid_agent_ids=valid_agent_ids,
            valid_chatroom_ids=valid_chatroom_ids,
        )

    async def trigger_run(
        self,
        workflow_id: uuid.UUID,
        *,
        trigger_type: str = "manual",
        started_by_user_id: uuid.UUID | None = None,
        trigger_payload: dict[str, Any] | None = None,
        project_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return await self._svc.trigger_run(
            workflow_id,
            started_by_user_id=started_by_user_id,
            trigger_payload=trigger_payload,
            project_id=project_id,
        )

    async def get_run(self, run_id: uuid.UUID) -> WorkflowRun | None:
        try:
            return await self._svc.get_run(run_id)
        except Exception:
            return None

    async def get_run_project_id(self, run_id: uuid.UUID) -> uuid.UUID | None:
        row = (
            await self._db.execute(
                sa.select(_workflow_runs_table.c.project_id).where(_workflow_runs_table.c.id == run_id)
            )
        ).first()
        return row.project_id if row else None

    async def cancel_run(self, run_id: uuid.UUID) -> None:
        await self._engine.cancel_run(run_id)

    async def list_steps(self, run_id: uuid.UUID) -> list[WorkflowStep]:
        return await self._svc.list_steps(run_id)


__all__ = ["WorkflowFacade"]
