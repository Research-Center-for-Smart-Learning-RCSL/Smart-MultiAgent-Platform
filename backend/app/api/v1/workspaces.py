"""`/api/projects/{pid}/workspaces` + `/api/workspaces/{id}` — F.1 / §22.10."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.workspace_service import WorkspaceService
from contexts.conversation.domain.errors import WorkspaceNotFound
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    current_context,
    current_principal,
    require,
    require_membership,
    scope_from_path,
)
from shared_kernel.auth.permissions import Capability, Principal
from shared_kernel.db.session import db_session

project_router = APIRouter(prefix="/api/projects", tags=["workspaces"])
workspace_router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    default_chatroom_name: str = Field("general", min_length=1, max_length=200)


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    created_at: str
    deleted_at: str | None


class WorkspaceCreatedOut(WorkspaceOut):
    default_chatroom_id: uuid.UUID


def _to_out(ws) -> WorkspaceOut:  # type: ignore[no-untyped-def]
    return WorkspaceOut(
        id=ws.id,
        project_id=ws.project_id,
        name=ws.name,
        created_at=ws.created_at.isoformat(),
        deleted_at=ws.deleted_at.isoformat() if ws.deleted_at else None,
    )


@project_router.get("/{project_id}/workspaces")
async def list_workspaces(
    project_id: uuid.UUID = Path(...),
    _=Depends(require_membership(project_param="project_id")),
    db: AsyncSession = Depends(db_session),
) -> list[WorkspaceOut]:
    service = WorkspaceService(db)
    rows = await service.list_for_project(project_id)
    return [_to_out(r) for r in rows]


@project_router.post(
    "/{project_id}/workspaces", status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    body: WorkspaceCreateIn,
    project_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    _=Depends(require(
        Capability.RESOURCE_CREATE_EDIT,
        scope_from_path(project_param="project_id"),
    )),
    db: AsyncSession = Depends(db_session),
) -> WorkspaceCreatedOut:
    service = WorkspaceService(db)
    result = await service.create(
        project_id=project_id,
        name=body.name,
        default_room_name=body.default_chatroom_name,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    out = _to_out(result.workspace)
    return WorkspaceCreatedOut(
        **out.model_dump(),
        default_chatroom_id=result.default_chatroom.id,
    )


@workspace_router.delete(
    "/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_workspace(
    workspace_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    # Resolve parent project, then do the capability check in-band so the
    # route stays a single sequential chain (the chatroom/message routers
    # do the same — keeps AuthZ right next to the facade lookup).
    facade = ConversationFacade(db)
    ws = await facade.get_workspace(workspace_id)
    if ws is None:
        raise WorkspaceNotFound(str(workspace_id))
    from shared_kernel.auth.dependencies import (  # noqa: PLC0415
        _raise_forbidden, get_role_resolver,
    )
    from shared_kernel.auth.permissions import Scope, decide  # noqa: PLC0415
    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        Capability.RESOURCE_CREATE_EDIT,
        Scope(project_id=ws.project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)

    service = WorkspaceService(db)
    await service.soft_delete(
        workspace_id=workspace_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["project_router", "workspace_router"]
