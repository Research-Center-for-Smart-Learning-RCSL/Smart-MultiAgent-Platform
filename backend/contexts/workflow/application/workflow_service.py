"""Workflow application service — CRUD + validation + run trigger."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import jsonschema
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.linter import validate_definition
from contexts.workflow.application.run_engine import RunEngine
from contexts.workflow.domain.errors import (
    WorkflowDeleted,
    WorkflowNotFound,
    WorkflowRunNotCancellable,
    WorkflowRunNotFound,
    WorkflowValidationFailed,
    WorkflowVersionConflict,
)
from contexts.workflow.domain.models import (
    LintIssue,
    RunState,
    ValidationResult,
    Workflow,
    WorkflowRun,
    WorkflowStep,
)
from contexts.workflow.infrastructure.repositories import (
    WorkflowRepository,
    WorkflowRunArchiveRepository,
    WorkflowRunRepository,
    WorkflowStepRepository,
)
from shared_kernel import audit
from shared_kernel.db.restore import raise_restore_conflict

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "workflow.schema.json"
_SCHEMA: dict[str, Any] | None = None


def _get_schema() -> dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _SCHEMA


class WorkflowService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = WorkflowRepository(db)
        self._runs = WorkflowRunRepository(db)
        self._steps = WorkflowStepRepository(db)
        self._archive = WorkflowRunArchiveRepository(db)
        # DB-1: the engine used by the last trigger_run(); dispatch_pending()
        # flushes its queued Arq jobs after the caller commits.
        self._engine: RunEngine | None = None

    # -- scope resolution (API-1) --

    async def resolve_workflow_scope(
        self,
        workflow_id: uuid.UUID,
    ) -> uuid.UUID:
        """Return the ``project_id`` that owns ``workflow_id``.

        Soft-deleted workflows are still resolved so the precise domain
        error (404/410) is produced by downstream handlers, not masked by
        the auth layer.

        Raises ``WorkflowNotFound`` if neither the workflow nor its
        workspace exist.
        """
        from contexts.conversation.interfaces.facade import ConversationFacade

        wf = await self._repo.get(workflow_id, include_deleted=True)
        if wf is None:
            raise WorkflowNotFound(f"Workflow {workflow_id} not found")
        facade = ConversationFacade(self._db)
        ws = await facade.get_workspace(wf.workspace_id)
        if ws is None:
            raise WorkflowNotFound(f"Workspace for workflow {workflow_id} not found")
        return ws.project_id

    async def resolve_run_scope(
        self,
        run_id: uuid.UUID,
    ) -> uuid.UUID:
        """Return the ``project_id`` that owns a workflow run.

        Raises ``WorkflowRunNotFound`` if the run does not exist.
        """
        project_id = await self._runs.get_project_id(run_id)
        if project_id is None:
            raise WorkflowRunNotFound(f"Workflow run {run_id} not found")
        return project_id

    # -- CRUD --

    async def list_for_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Workflow]:
        return await self._repo.list_for_workspace(
            workspace_id,
            limit=limit,
            offset=offset,
        )

    async def get(self, workflow_id: uuid.UUID, *, include_deleted: bool = False) -> Workflow:
        wf = await self._repo.get(workflow_id, include_deleted=include_deleted)
        if not wf:
            raise WorkflowNotFound(f"Workflow {workflow_id} not found")
        return wf

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        definition: dict[str, Any],
        actor_user_id: uuid.UUID | None = None,
        valid_agent_ids: frozenset[str] = frozenset(),
        valid_chatroom_ids: frozenset[str] = frozenset(),
        subagent_parent_ids: frozenset[str] = frozenset(),
    ) -> Workflow:
        self._validate_schema(definition)
        result = validate_definition(
            definition,
            valid_agent_ids=valid_agent_ids,
            valid_chatroom_ids=valid_chatroom_ids,
            subagent_parent_ids=subagent_parent_ids,
        )
        if not result.valid:
            raise WorkflowValidationFailed(
                [_issue_to_dict(e) for e in result.errors],
                [_issue_to_dict(w) for w in result.warnings],
            )

        wf = await self._repo.insert(
            workspace_id=workspace_id,
            name=name,
            definition=definition,
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.created",
                resource_type="workflow",
                resource_id=wf.id,
                actor_user_id=actor_user_id,
                metadata={"workspace_id": str(workspace_id), "name": name},
            ),
        )
        return wf

    async def patch(
        self,
        workflow_id: uuid.UUID,
        *,
        expected_version: int,
        name: str | None = None,
        definition: dict[str, Any] | None = None,
        actor_user_id: uuid.UUID | None = None,
        valid_agent_ids: frozenset[str] = frozenset(),
        valid_chatroom_ids: frozenset[str] = frozenset(),
        subagent_parent_ids: frozenset[str] = frozenset(),
    ) -> Workflow:
        if definition is not None:
            self._validate_schema(definition)
            result = validate_definition(
                definition,
                valid_agent_ids=valid_agent_ids,
                valid_chatroom_ids=valid_chatroom_ids,
                subagent_parent_ids=subagent_parent_ids,
            )
            if not result.valid:
                raise WorkflowValidationFailed(
                    [_issue_to_dict(e) for e in result.errors],
                    [_issue_to_dict(w) for w in result.warnings],
                )

        wf = await self._repo.update(
            workflow_id,
            expected_version=expected_version,
            name=name,
            definition=definition,
        )
        if wf is None:
            existing = await self._repo.get(workflow_id, include_deleted=True)
            if existing is None:
                raise WorkflowNotFound(f"Workflow {workflow_id} not found")
            if existing.deleted_at is not None:
                raise WorkflowDeleted(f"Workflow {workflow_id} is soft-deleted")
            raise WorkflowVersionConflict(
                f"Expected version {expected_version}, current is {existing.version}",
            )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.edited",
                resource_type="workflow",
                resource_id=workflow_id,
                actor_user_id=actor_user_id,
                metadata={"version": wf.version},
            ),
        )
        return wf

    async def soft_delete(
        self,
        workflow_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        deleted = await self._repo.soft_delete(workflow_id)
        if not deleted:
            raise WorkflowNotFound(f"Workflow {workflow_id} not found")

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.deleted",
                resource_type="workflow",
                resource_id=workflow_id,
                actor_user_id=actor_user_id,
            ),
        )

    async def admin_restore(
        self,
        workflow_id: uuid.UUID,
        *,
        admin_user_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted workflow definition (R8.13): a pure
        deleted_at clear emitting admin.restore_resource. Clearing deleted_at can
        violate the uq_workflows_workspace_id_name partial-unique index if a live
        workflow reused the (workspace, name) while this one was deleted — surfaced
        as RestoreConflict for the route to map to 409."""
        try:
            restored = await self._repo.restore(workflow_id)
        except IntegrityError as exc:
            raise_restore_conflict(
                exc, unique_constraint="uq_workflows_workspace_id_name", resource_type="workflow"
            )
        if not restored:
            return False
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.restore_resource",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="workflow",
                resource_id=workflow_id,
                request_id=request_id,
            ),
        )
        return True

    # -- Validation (no persist) --

    def validate(
        self,
        definition: dict[str, Any],
        *,
        valid_agent_ids: frozenset[str] = frozenset(),
        valid_chatroom_ids: frozenset[str] = frozenset(),
        subagent_parent_ids: frozenset[str] = frozenset(),
    ) -> ValidationResult:
        schema_errors = self._validate_schema_collect(definition)
        if schema_errors:
            return ValidationResult(
                valid=False,
                errors=[LintIssue(0, "error", e.message) for e in schema_errors],
                warnings=[],
            )
        return validate_definition(
            definition,
            valid_agent_ids=valid_agent_ids,
            valid_chatroom_ids=valid_chatroom_ids,
            subagent_parent_ids=subagent_parent_ids,
        )

    # -- Runs --

    async def trigger_run(
        self,
        workflow_id: uuid.UUID,
        *,
        started_by_user_id: uuid.UUID | None = None,
        trigger_payload: dict[str, Any] | None = None,
        project_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        wf = await self.get(workflow_id)
        defn = wf.definition
        trigger_type = "manual"
        trigger_nodes = [n for n in defn.get("nodes", []) if n.get("type") == "trigger"]
        if trigger_nodes:
            trigger_type = trigger_nodes[0].get("config", {}).get("trigger_type", "manual")

        # H.4/K.4: a run's project_id is a NOT-NULL FK. The API path supplies it
        # from the request scope; the cron scheduler (and any other
        # project-less trigger) does not, so derive it from the workflow's
        # workspace. The former ``UUID(int=0)`` fallback violated the FK and
        # failed every cron-triggered run at insert.
        pid = project_id
        if pid is None:
            pid = await self._repo.resolve_project_id(workflow_id)
            if pid is None:
                raise WorkflowNotFound(
                    f"Cannot resolve project for workflow {workflow_id} (workspace missing?)"
                )

        self._engine = RunEngine(self._db)
        return await self._engine.start_run(
            project_id=pid,
            workflow_id=workflow_id,
            definition=defn,
            trigger_type=trigger_type,
            started_by_user_id=started_by_user_id,
            trigger_payload=trigger_payload,
        )

    async def dry_run(
        self,
        workflow_id: uuid.UUID,
        *,
        started_by_user_id: uuid.UUID,
        trigger_payload: dict[str, Any] | None = None,
        project_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Simulate workflow execution without side effects (R14.06)."""
        wf = await self.get(workflow_id)
        defn = wf.definition

        result = self.validate(defn)
        if result.errors:
            raise WorkflowValidationFailed([_issue_to_dict(e) for e in result.errors])

        pid = project_id
        if pid is None:
            pid = await self._repo.resolve_project_id(workflow_id)
            if pid is None:
                raise WorkflowNotFound(f"Cannot resolve project for workflow {workflow_id}")

        self._engine = RunEngine(self._db)
        return await self._engine.start_run(
            project_id=pid,
            workflow_id=workflow_id,
            definition=defn,
            trigger_type="dry_run",
            started_by_user_id=started_by_user_id,
            trigger_payload=trigger_payload,
            is_dry_run=True,
        )

    async def dispatch_pending(self, pool: Any | None = None) -> None:
        """Enqueue the Arq jobs queued by the last ``trigger_run`` (DB-1).

        Must be called by the entry point *after* it commits, so a worker never
        picks up a job that references an uncommitted run. A no-op if no run was
        triggered through this service instance.

        ASYNC-6: a worker entry point passes its own ``ctx["redis"]`` pool so
        no Arq connection pool is opened (and leaked) per dispatch.
        """
        if self._engine is not None:
            await self._engine.dispatch_enqueues(pool)

    async def get_run(self, run_id: uuid.UUID) -> WorkflowRun:
        run = await self._runs.get(run_id)
        if not run:
            raise WorkflowRunNotFound(f"Workflow run {run_id} not found")
        return run

    async def list_runs(
        self,
        workflow_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        include_archive: bool = False,
    ) -> list[WorkflowRun] | list[dict[str, Any]]:
        if include_archive:
            return await self._archive.list_union_for_workflow(
                workflow_id,
                limit=limit,
                offset=offset,
            )
        return await self._runs.list_for_workflow(workflow_id, limit=limit, offset=offset)

    async def cancel_run(
        self,
        run_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        run = await self._runs.get(run_id)
        if not run:
            raise WorkflowRunNotFound(f"Workflow run {run_id} not found")
        if run.state not in (RunState.RUNNING, RunState.WAITING):
            raise WorkflowRunNotCancellable(
                f"Run {run_id} is in state {run.state.value}, cannot cancel",
            )
        self._engine = RunEngine(self._db)
        if not await self._engine.cancel_run(run_id):
            raise WorkflowRunNotCancellable(f"Run {run_id} completed before it could be cancelled")

    async def list_steps(self, run_id: uuid.UUID) -> list[WorkflowStep]:
        return await self._steps.list_for_run(run_id)

    # -- JSON Schema --

    def _validate_schema(self, definition: dict[str, Any]) -> None:
        schema = _get_schema()
        try:
            jsonschema.validate(definition, schema)
        except jsonschema.ValidationError as exc:
            raise WorkflowValidationFailed(
                [{"rule": 0, "level": "error", "message": exc.message}],
            ) from exc

    def _validate_schema_collect(
        self,
        definition: dict[str, Any],
    ) -> list[jsonschema.ValidationError]:
        schema = _get_schema()
        validator = jsonschema.Draft202012Validator(schema)
        return list(validator.iter_errors(definition))


def _issue_to_dict(issue: Any) -> dict[str, Any]:
    return {
        "rule": issue.rule,
        "level": issue.level,
        "message": issue.message,
        "node_id": issue.node_id,
        "edge_id": issue.edge_id,
    }
