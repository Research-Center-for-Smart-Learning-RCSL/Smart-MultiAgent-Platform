"""`/api/workspaces/{wid}/workflows` + `/api/workflows/{id}` — H.1 / §22.11.

AuthZ (API-1): cap #16 (CHAT_CREATE) for create/edit/delete/trigger/cancel;
project membership for read (list/get/validate/list_runs/list_steps). Every
workflow- and run-scoped endpoint resolves its target to a `project_id` first
(`_resolve_workflow` / `_resolve_run`) so an authenticated caller cannot reach
another tenant's workflow by enumerating UUIDs.
Runs: trigger via POST /api/workflows/{id}/runs; cancel via POST /api/workflow-runs/{id}/cancel.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.workflow.application.workflow_service import WorkflowService
from contexts.workflow.domain.errors import (
    WorkflowError,  # noqa: F401 — error_mapping catches these
    WorkflowNotFound,
    WorkflowRunNotFound,
)
from shared_kernel.auth.dependencies import (
    current_principal,
    get_role_resolver,
)
from shared_kernel.auth.permissions import (
    Capability,
    Principal,
    RoleResolver,
    Scope,
    decide,
)
from shared_kernel.db.session import db_session

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

workspace_router = APIRouter(prefix="/api/workspaces", tags=["workflows"])
workflow_router = APIRouter(prefix="/api/workflows", tags=["workflows"])
run_router = APIRouter(prefix="/api/workflow-runs", tags=["workflow-runs"])


# ---------------------------------------------------------------------------
# Scope resolution for auth — every workflow/run endpoint lifts its target to
# the owning project so `decide(...)` / membership can be checked (API-1).
# ---------------------------------------------------------------------------


async def _resolve_workspace(
    wid: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> Scope:
    """Resolve workspace_id → project_id and return a Scope."""
    facade = ConversationFacade(db)
    ws = await facade.get_workspace(wid)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return Scope(project_id=ws.project_id)


async def _resolve_workflow(
    workflow_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> Scope:
    """Resolve workflow_id → its workspace's project_id for the authz check.

    Soft-deleted workflows are still resolved so the precise domain error
    (404/410) is produced by the service, not masked by the auth layer.
    """
    svc = WorkflowService(db)
    try:
        project_id = await svc.resolve_workflow_scope(workflow_id)
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail="Workflow not found") from None
    return Scope(project_id=project_id)


async def _resolve_run(
    run_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
) -> Scope:
    """Resolve run_id → its workflow run's project_id for the authz check."""
    svc = WorkflowService(db)
    try:
        project_id = await svc.resolve_run_scope(run_id)
    except WorkflowRunNotFound:
        raise HTTPException(status_code=404, detail="Workflow run not found") from None
    return Scope(project_id=project_id)


async def _require_member(
    principal: Principal,
    scope: Scope,
    resolver: RoleResolver,
) -> None:
    """Read access: caller must hold any role in the workflow's project."""
    if principal.is_admin:
        return
    roles = await resolver.roles_for(principal, scope)
    if not roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="caller is not a member of the workflow's project",
        )


async def _require_chat_create(
    principal: Principal,
    scope: Scope,
    resolver: RoleResolver,
) -> None:
    """Mutating access: caller needs CHAT_CREATE (§5.2 cap #16) at the project."""
    decision = await decide(principal, Capability.CHAT_CREATE, scope, resolver)
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason)


async def _linter_valid_ids(
    db: AsyncSession,
    project_id: uuid.UUID | None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Build the (agent ids, chatroom ids) sets the linter scopes references
    against (rules 6 & 8).

    F1: without these the service defaulted both to empty, so the linter
    rejected *every* workflow that referenced an agent or chatroom — i.e. any
    non-trivial workflow failed to save. The ids are scoped to the workflow's
    own project, so a reference to another tenant's agent/chatroom is still
    rejected. `subagent_parent_ids` stays empty by design: sub-agents are
    runtime AgentInstances (G.8), not agent definitions, so there is no
    save-time set — depth>1 is enforced at spawn time by the orchestration
    service, not here.
    """
    if project_id is None:  # defensive — the resolvers always set it
        return frozenset(), frozenset()
    agents = await AgentsFacade(db).list_agents_for_project(project_id)
    chatroom_ids = await ConversationFacade(db).list_chatroom_ids_for_project(project_id)
    return (
        frozenset(str(a.id) for a in agents),
        frozenset(str(cid) for cid in chatroom_ids),
    )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class WorkflowCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: dict[str, Any]


class WorkflowPatchIn(BaseModel):
    model_config = {"extra": "forbid"}
    name: str | None = Field(default=None, min_length=1, max_length=200)
    definition: dict[str, Any] | None = None


class WorkflowOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    definition: dict[str, Any]
    version: int
    created_at: str
    deleted_at: str | None


class RunTriggerIn(BaseModel):
    trigger_payload: dict[str, Any] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    trigger_type: str
    started_by_user_id: uuid.UUID | None
    state: str
    variables: dict[str, Any]
    started_at: str
    ended_at: str | None


class ArchivedRunOut(BaseModel):
    """A run row from the live+archive union (`list_runs?include_archive=true`).

    Distinct from `RunOut` (API-6): the archive projection omits `variables`,
    and the `archived` flag tells the client which table the row came from.
    Defining it explicitly means archive rows are schema-validated instead of
    being returned as raw service dicts.
    """

    id: uuid.UUID
    workflow_id: uuid.UUID | None
    trigger_type: str | None
    started_by_user_id: uuid.UUID | None
    state: str
    started_at: str
    ended_at: str | None
    archived: bool


class StepOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    node_id: str
    state: str
    started_at: str
    ended_at: str | None
    input: dict[str, Any]
    output: dict[str, Any]
    error: str | None


class ValidateIn(BaseModel):
    definition: dict[str, Any]


class ValidateOut(BaseModel):
    valid: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_workflow_out(wf: Any) -> WorkflowOut:
    return WorkflowOut(
        id=wf.id,
        workspace_id=wf.workspace_id,
        name=wf.name,
        definition=wf.definition,
        version=wf.version,
        created_at=wf.created_at.isoformat(),
        deleted_at=wf.deleted_at.isoformat() if wf.deleted_at else None,
    )


def _to_run_out(run: Any) -> RunOut:
    return RunOut(
        id=run.id,
        workflow_id=run.workflow_id,
        trigger_type=run.trigger_type,
        started_by_user_id=run.started_by_user_id,
        state=run.state.value if hasattr(run.state, "value") else str(run.state),
        variables=run.variables,
        started_at=run.started_at.isoformat(),
        ended_at=run.ended_at.isoformat() if run.ended_at else None,
    )


def _iso(value: Any) -> str | None:
    """Render a datetime (or None) as an ISO-8601 string."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _to_archived_run_out(row: dict[str, Any]) -> ArchivedRunOut:
    return ArchivedRunOut(
        id=row["id"],
        workflow_id=row["workflow_id"],
        trigger_type=row["trigger_type"],
        started_by_user_id=row["started_by_user_id"],
        state=row["state"],
        started_at=_iso(row["started_at"]) or "",
        ended_at=_iso(row["ended_at"]),
        archived=row["archived"],
    )


def _to_step_out(step: Any) -> StepOut:
    return StepOut(
        id=step.id,
        run_id=step.run_id,
        node_id=step.node_id,
        state=step.state.value if hasattr(step.state, "value") else str(step.state),
        started_at=step.started_at.isoformat(),
        ended_at=step.ended_at.isoformat() if step.ended_at else None,
        input=step.input,
        output=step.output,
        error=step.error,
    )


# ---------------------------------------------------------------------------
# Workspace-scoped endpoints
# ---------------------------------------------------------------------------


@workspace_router.get("/{wid}/workflows")
async def list_workflows(
    wid: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    scope: Scope = Depends(_resolve_workspace),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> list[WorkflowOut]:
    await _require_member(principal, scope, resolver)
    svc = WorkflowService(db)
    workflows = await svc.list_for_workspace(
        wid,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return [_to_workflow_out(w) for w in workflows]


@workspace_router.post(
    "/{wid}/workflows/validate",
    status_code=status.HTTP_200_OK,
)
async def validate_workflow(
    payload: ValidateIn,
    wid: uuid.UUID = Path(...),
    scope: Scope = Depends(_resolve_workspace),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> ValidateOut:
    await _require_member(principal, scope, resolver)
    svc = WorkflowService(db)
    valid_agent_ids, valid_chatroom_ids = await _linter_valid_ids(db, scope.project_id)
    result = svc.validate(
        payload.definition,
        valid_agent_ids=valid_agent_ids,
        valid_chatroom_ids=valid_chatroom_ids,
    )
    return ValidateOut(
        valid=result.valid,
        errors=[
            {
                "rule": e.rule,
                "level": e.level,
                "message": e.message,
                "node_id": e.node_id,
                "edge_id": e.edge_id,
            }
            for e in result.errors
        ],
        warnings=[
            {
                "rule": w.rule,
                "level": w.level,
                "message": w.message,
                "node_id": w.node_id,
                "edge_id": w.edge_id,
            }
            for w in result.warnings
        ],
    )


@workspace_router.post(
    "/{wid}/workflows",
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow(
    payload: WorkflowCreateIn,
    wid: uuid.UUID = Path(...),
    scope: Scope = Depends(_resolve_workspace),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> WorkflowOut:
    await _require_chat_create(principal, scope, resolver)
    svc = WorkflowService(db)
    valid_agent_ids, valid_chatroom_ids = await _linter_valid_ids(db, scope.project_id)
    wf = await svc.create(
        workspace_id=wid,
        name=payload.name,
        definition=payload.definition,
        actor_user_id=principal.user_id,
        valid_agent_ids=valid_agent_ids,
        valid_chatroom_ids=valid_chatroom_ids,
    )
    return _to_workflow_out(wf)


# ---------------------------------------------------------------------------
# Workflow-scoped endpoints
# ---------------------------------------------------------------------------


@workflow_router.patch("/{workflow_id}")
async def patch_workflow(
    payload: WorkflowPatchIn,
    workflow_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    scope: Scope = Depends(_resolve_workflow),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> WorkflowOut:
    await _require_chat_create(principal, scope, resolver)
    try:
        expected_version = int(if_match)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="If-Match header must be an integer version",
        ) from exc

    svc = WorkflowService(db)
    valid_agent_ids, valid_chatroom_ids = await _linter_valid_ids(db, scope.project_id)
    wf = await svc.patch(
        workflow_id,
        expected_version=expected_version,
        name=payload.name,
        definition=payload.definition,
        actor_user_id=principal.user_id,
        valid_agent_ids=valid_agent_ids,
        valid_chatroom_ids=valid_chatroom_ids,
    )
    return _to_workflow_out(wf)


@workflow_router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_workflow(
    workflow_id: uuid.UUID = Path(...),
    scope: Scope = Depends(_resolve_workflow),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _require_chat_create(principal, scope, resolver)
    svc = WorkflowService(db)
    await svc.soft_delete(workflow_id, actor_user_id=principal.user_id)


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------


@workflow_router.post(
    "/{workflow_id}/runs",
    status_code=status.HTTP_201_CREATED,
)
async def trigger_run(
    payload: RunTriggerIn,
    workflow_id: uuid.UUID = Path(...),
    scope: Scope = Depends(_resolve_workflow),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    await _require_chat_create(principal, scope, resolver)
    svc = WorkflowService(db)
    run_id = await svc.trigger_run(
        workflow_id,
        started_by_user_id=principal.user_id,
        trigger_payload=payload.trigger_payload,
        project_id=scope.project_id,
    )
    # DB-1: commit the run + its steps before dispatching Arq jobs, so a worker
    # that picks up a parallel branch can see the committed run row. The
    # trailing commit in the db_session dependency is then a no-op.
    await db.commit()
    await svc.dispatch_pending()
    return {"run_id": str(run_id)}


@workflow_router.get("/{workflow_id}/runs")
async def list_runs(
    workflow_id: uuid.UUID = Path(...),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_archive: bool = Query(False),
    scope: Scope = Depends(_resolve_workflow),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> list[RunOut | ArchivedRunOut]:
    await _require_member(principal, scope, resolver)
    svc = WorkflowService(db)
    runs = await svc.list_runs(
        workflow_id,
        limit=limit,
        offset=offset,
        include_archive=include_archive,
    )
    if include_archive:
        # Archive path returns the live+archive union as dicts (API-6).
        archive_rows = cast("list[dict[str, Any]]", runs)
        return [_to_archived_run_out(r) for r in archive_rows]
    return [_to_run_out(r) for r in runs]


@run_router.get("/{run_id}")
async def get_run(
    run_id: uuid.UUID = Path(...),
    scope: Scope = Depends(_resolve_run),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> RunOut:
    await _require_member(principal, scope, resolver)
    svc = WorkflowService(db)
    run = await svc.get_run(run_id)
    return _to_run_out(run)


@run_router.post(
    "/{run_id}/cancel",
    status_code=status.HTTP_200_OK,
)
async def cancel_run(
    run_id: uuid.UUID = Path(...),
    scope: Scope = Depends(_resolve_run),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    await _require_chat_create(principal, scope, resolver)
    svc = WorkflowService(db)
    await svc.cancel_run(run_id, actor_user_id=principal.user_id)
    return {"status": "cancelled"}


@run_router.get("/{run_id}/steps")
async def list_steps(
    run_id: uuid.UUID = Path(...),
    scope: Scope = Depends(_resolve_run),
    principal: Principal = Depends(current_principal),
    resolver: RoleResolver = Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> list[StepOut]:
    await _require_member(principal, scope, resolver)
    svc = WorkflowService(db)
    steps = await svc.list_steps(run_id)
    return [_to_step_out(s) for s in steps]
