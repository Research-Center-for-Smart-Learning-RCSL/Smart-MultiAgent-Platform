"""Workflow facade — public surface for other contexts.

Other contexts (Orchestration, Conversation) use this facade to:
- Validate workflow definitions
- Trigger workflow runs
- Query run state
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
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
from shared_kernel.auth.clients import now

logger = logging.getLogger(__name__)


class WorkflowFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._svc = WorkflowService(db)
        self._engine = RunEngine(db)

    async def get_workflow(self, workflow_id: uuid.UUID) -> Workflow | None:
        try:
            return await self._svc.get(workflow_id)
        except Exception:
            logger.exception("Failed to fetch workflow %s", workflow_id)
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
            logger.exception("Failed to fetch workflow run %s", run_id)
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

    # -- Retention helpers (H4) ------------------------------------------------

    async def archive_old_runs(self, *, retention_days: int = 90) -> int:
        """Archive workflow runs ended > *retention_days* ago.

        Writes the full archive row and is idempotent (``ON CONFLICT DO
        NOTHING``). After archiving, deletes the steps and source rows.
        """
        cutoff = now() - timedelta(days=retention_days)
        result = await self._db.execute(
            sa.text(
                "INSERT INTO workflow_runs_archive "
                "(id, workflow_id, trigger_type, started_by_user_id, state, "
                " started_at, ended_at, summary) "
                "SELECT wr.id, wr.workflow_id, wr.trigger_type, wr.started_by_user_id, "
                "       wr.state, wr.started_at, wr.ended_at, "
                "       jsonb_build_object("
                "         'node_count', "
                "         (SELECT count(*) FROM workflow_steps ws WHERE ws.run_id = wr.id), "
                "         'failures', "
                "         (SELECT count(*) FROM workflow_steps ws "
                "          WHERE ws.run_id = wr.id AND ws.state = 'failed')"
                "       ) "
                "FROM workflow_runs wr "
                "WHERE wr.ended_at IS NOT NULL AND wr.ended_at < :cutoff "
                "  AND wr.id NOT IN (SELECT id FROM workflow_runs_archive) "
                "LIMIT 500 "
                "ON CONFLICT (id) DO NOTHING"
            ).bindparams(cutoff=cutoff)
        )
        archived = result.rowcount or 0  # type: ignore[attr-defined]
        if archived > 0:
            await self._db.execute(
                sa.text(
                    "DELETE FROM workflow_steps WHERE run_id IN ("
                    "  SELECT wr.id FROM workflow_runs wr"
                    "  JOIN workflow_runs_archive wra ON wra.id = wr.id"
                    "  WHERE wr.ended_at IS NOT NULL AND wr.ended_at < :cutoff"
                    ")"
                ).bindparams(cutoff=cutoff)
            )
            await self._db.execute(
                sa.text(
                    "DELETE FROM workflow_runs WHERE id IN ("
                    "  SELECT wr.id FROM workflow_runs wr"
                    "  JOIN workflow_runs_archive wra ON wra.id = wr.id"
                    "  WHERE wr.ended_at IS NOT NULL AND wr.ended_at < :cutoff"
                    ")"
                ).bindparams(cutoff=cutoff)
            )
        return archived


__all__ = ["WorkflowFacade"]
