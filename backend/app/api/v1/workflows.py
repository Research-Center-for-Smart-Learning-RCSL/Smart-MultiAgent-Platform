"""`/api/workspaces/{wid}/workflows` + `/api/workflows/{id}` — H.1 / §22.11.

AuthZ: cap #16 (CHAT_CREATE) for create/edit/delete; membership for read.
Runs: trigger via POST /api/workflows/{id}/runs; cancel via POST /api/workflow-runs/{id}/cancel.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.workflow_service import WorkflowService
from contexts.workflow.domain.errors import WorkflowError  # noqa: F401 — error_mapping catches these
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.dependencies import (
    current_principal,
    get_role_resolver,
)
from shared_kernel.auth.permissions import Capability, Principal, Scope, decide
from shared_kernel.db.session import db_session


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

workspace_router = APIRouter(prefix="/api/workspaces", tags=["workflows"])
workflow_router = APIRouter(prefix="/api/workflows", tags=["workflows"])
run_router = APIRouter(prefix="/api/workflow-runs", tags=["workflow-runs"])


# ---------------------------------------------------------------------------
# Workspace → project resolution for auth
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
    scope: Scope = Depends(_resolve_workspace),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[WorkflowOut]:
    # Membership check: principal is in the workspace's project
    svc = WorkflowService(db)
    workflows = await svc.list_for_workspace(wid)
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
    db: AsyncSession = Depends(db_session),
) -> ValidateOut:
    svc = WorkflowService(db)
    result = svc.validate(payload.definition)
    return ValidateOut(
        valid=result.valid,
        errors=[
            {"rule": e.rule, "level": e.level, "message": e.message,
             "node_id": e.node_id, "edge_id": e.edge_id}
            for e in result.errors
        ],
        warnings=[
            {"rule": w.rule, "level": w.level, "message": w.message,
             "node_id": w.node_id, "edge_id": w.edge_id}
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
    resolver=Depends(get_role_resolver),
    db: AsyncSession = Depends(db_session),
) -> WorkflowOut:
    decision = await decide(principal, Capability.CHAT_CREATE, scope, resolver)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    svc = WorkflowService(db)
    wf = await svc.create(
        workspace_id=wid,
        name=payload.name,
        definition=payload.definition,
        actor_user_id=principal.user_id,
    )
    await db.commit()
    return _to_workflow_out(wf)


# ---------------------------------------------------------------------------
# Workflow-scoped endpoints
# ---------------------------------------------------------------------------


@workflow_router.patch("/{workflow_id}")
async def patch_workflow(
    payload: WorkflowPatchIn,
    workflow_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> WorkflowOut:
    try:
        expected_version = int(if_match)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="If-Match header must be an integer version",
        )

    svc = WorkflowService(db)
    wf = await svc.patch(
        workflow_id,
        expected_version=expected_version,
        name=payload.name,
        definition=payload.definition,
        actor_user_id=principal.user_id,
    )
    await db.commit()
    return _to_workflow_out(wf)


@workflow_router.delete(
    "/{workflow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_workflow(
    workflow_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    svc = WorkflowService(db)
    await svc.soft_delete(workflow_id, actor_user_id=principal.user_id)
    await db.commit()


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
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    svc = WorkflowService(db)
    run_id = await svc.trigger_run(
        workflow_id,
        started_by_user_id=principal.user_id,
        trigger_payload=payload.trigger_payload,
    )
    await db.commit()
    return {"run_id": str(run_id)}


@workflow_router.get("/{workflow_id}/runs")
async def list_runs(
    workflow_id: uuid.UUID = Path(...),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_archive: bool = Query(False),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[RunOut | dict]:
    svc = WorkflowService(db)
    runs = await svc.list_runs(
        workflow_id, limit=limit, offset=offset, include_archive=include_archive,
    )
    if include_archive:
        return runs  # type: ignore[return-value]
    return [_to_run_out(r) for r in runs]


@run_router.get("/{run_id}")
async def get_run(
    run_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> RunOut:
    svc = WorkflowService(db)
    run = await svc.get_run(run_id)
    return _to_run_out(run)


@run_router.post(
    "/{run_id}/cancel",
    status_code=status.HTTP_200_OK,
)
async def cancel_run(
    run_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    svc = WorkflowService(db)
    await svc.cancel_run(run_id, actor_user_id=principal.user_id)
    await db.commit()
    return {"status": "cancelled"}


@run_router.get("/{run_id}/steps")
async def list_steps(
    run_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[StepOut]:
    svc = WorkflowService(db)
    steps = await svc.list_steps(run_id)
    return [_to_step_out(s) for s in steps]
