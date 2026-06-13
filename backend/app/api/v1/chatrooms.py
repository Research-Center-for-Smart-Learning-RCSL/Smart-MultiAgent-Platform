"""`/api/workspaces/{id}/chatrooms` + `/api/chatrooms/*` — F.2 / §22.10."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.application.chatroom_service import (
    ChatroomFlagsPatch,
    ChatroomService,
)
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    WorkspaceNotFound,
)
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
    get_role_resolver,
)
from shared_kernel.auth.permissions import (
    Capability,
    Principal,
    Scope,
    decide,
)
from shared_kernel.db.session import db_session

workspace_router = APIRouter(prefix="/api/workspaces", tags=["chatrooms"])
chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["chatrooms"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class ChatroomCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    allow_org_members: bool = False
    allow_project_members: bool = True
    allow_project_owners_only: bool = False
    allow_guest_links: bool = False


class ChatroomPatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    allow_org_members: bool | None = None
    allow_project_members: bool | None = None
    allow_project_owners_only: bool | None = None
    allow_guest_links: bool | None = None


class ChatroomOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    allow_org_members: bool
    allow_project_members: bool
    allow_project_owners_only: bool
    allow_guest_links: bool
    version: int
    created_at: str
    deleted_at: str | None


class GuestLinkOut(BaseModel):
    url: str
    chatroom_id: uuid.UUID
    guest_token: str


class AgentRef(BaseModel):
    agent_id: uuid.UUID


def _to_out(r) -> ChatroomOut:
    return ChatroomOut(
        id=r.id,
        workspace_id=r.workspace_id,
        name=r.name,
        allow_org_members=r.allow_org_members,
        allow_project_members=r.allow_project_members,
        allow_project_owners_only=r.allow_project_owners_only,
        allow_guest_links=r.allow_guest_links,
        version=r.version,
        created_at=r.created_at.isoformat(),
        deleted_at=r.deleted_at.isoformat() if r.deleted_at else None,
    )


def _parse_if_match(header: str) -> int:
    try:
        return int(header.strip().strip('"'))
    except ValueError as exc:
        raise HTTPException(
            status_code=412,
            detail=f"invalid If-Match: {header!r}",
        ) from exc


async def _project_id_for_workspace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    ws = await ConversationFacade(db).get_workspace(workspace_id)
    if ws is None:
        raise WorkspaceNotFound(str(workspace_id))
    return ws.project_id


async def _project_id_for_chatroom(
    db: AsyncSession,
    chatroom_id: uuid.UUID,
) -> uuid.UUID:
    facade = ConversationFacade(db)
    room = await facade.get_chatroom(chatroom_id)
    if room is None:
        raise ChatroomNotFound(str(chatroom_id))
    ws = await facade.get_workspace(room.workspace_id)
    if ws is None:
        raise WorkspaceNotFound(str(room.workspace_id))
    return ws.project_id


async def _require_project_cap(
    db: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
    capability: Capability,
) -> None:
    resolver = await get_role_resolver(db)
    decision = await decide(
        principal,
        capability,
        Scope(project_id=project_id),
        resolver,
    )
    if not decision.allowed:
        _raise_forbidden(decision.reason)


# --------------------------------------------------------------------------- #
# List + create under workspace
# --------------------------------------------------------------------------- #


@workspace_router.get("/{workspace_id}/chatrooms")
async def list_chatrooms(
    workspace_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ChatroomOut]:
    project_id = await _project_id_for_workspace(db, workspace_id)
    # Any member of the parent project may enumerate the rooms. Admin bypass
    # lives in require_membership via principal.is_admin.
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(
            principal,
            Scope(project_id=project_id),
        )
        if not roles:
            _raise_forbidden("caller is not a member of the project")
    service = ChatroomService(db)
    rows = await service.list_for_workspace(workspace_id)
    return [_to_out(r) for r in rows]


@workspace_router.post(
    "/{workspace_id}/chatrooms",
    status_code=status.HTTP_201_CREATED,
)
async def create_chatroom(
    body: ChatroomCreateIn,
    workspace_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomOut:
    project_id = await _project_id_for_workspace(db, workspace_id)
    await _require_project_cap(db, principal, project_id, Capability.CHAT_CREATE)
    service = ChatroomService(db)
    room = await service.create(
        workspace_id=workspace_id,
        name=body.name,
        allow_org_members=body.allow_org_members,
        allow_project_members=body.allow_project_members,
        allow_project_owners_only=body.allow_project_owners_only,
        allow_guest_links=body.allow_guest_links,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(room)


# --------------------------------------------------------------------------- #
# Chatroom-scoped routes
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}")
async def read_chatroom(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomOut:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(
            principal,
            Scope(project_id=project_id, chatroom_id=chatroom_id),
        )
        is_guest = await ConversationFacade(db).is_chatroom_guest(
            chatroom_id=chatroom_id,
            user_id=principal.user_id,
        )
        if not roles and not is_guest:
            _raise_forbidden("not a participant of this room")
    service = ChatroomService(db)
    room = await service.get(chatroom_id)
    return _to_out(room)


@chatroom_router.patch("/{chatroom_id}")
async def patch_chatroom(
    body: ChatroomPatchIn,
    chatroom_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ChatroomOut:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.RESOURCE_CREATE_EDIT,
    )
    expected = _parse_if_match(if_match)
    service = ChatroomService(db)
    room = await service.patch(
        chatroom_id=chatroom_id,
        expected_version=expected,
        patch=ChatroomFlagsPatch(**body.model_dump(exclude_unset=True)),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(room)


@chatroom_router.delete(
    "/{chatroom_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_chatroom(
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.RESOURCE_CREATE_EDIT,
    )
    service = ChatroomService(db)
    await service.soft_delete(
        chatroom_id=chatroom_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


# --------------------------------------------------------------------------- #
# Agents subresource
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}/agents")
async def list_chatroom_agents(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[AgentRef]:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        if not await resolver.roles_for(
            principal,
            Scope(project_id=project_id),
        ):
            _raise_forbidden("not a member of the project")
    service = ChatroomService(db)
    rows = await service.list_agents(chatroom_id)
    return [AgentRef(agent_id=r.agent_id) for r in rows]


@chatroom_router.post(
    "/{chatroom_id}/agents",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def add_chatroom_agent(
    body: AgentRef,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.RESOURCE_CREATE_EDIT,
    )
    # Agents are project-scoped; a chatroom may only bind agents from its own
    # project. The picker UI already filters to in-project agents, but guard the
    # raw endpoint too so a direct call cannot create a cross-project binding.
    agent = await AgentsFacade(db).get_agent(body.agent_id)
    if agent is None or agent.project_id != project_id:
        raise HTTPException(
            status_code=422,
            detail="agent does not belong to this chatroom's project",
        )
    service = ChatroomService(db)
    await service.add_agent(
        chatroom_id=chatroom_id,
        agent_id=body.agent_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


@chatroom_router.delete(
    "/{chatroom_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def remove_chatroom_agent(
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.RESOURCE_CREATE_EDIT,
    )
    service = ChatroomService(db)
    await service.remove_agent(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


# --------------------------------------------------------------------------- #
# Guest link — R13.05–R13.07
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}/guest-link")
async def read_guest_link(
    request: Request,
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GuestLinkOut:
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(
        db,
        principal,
        project_id,
        Capability.GUEST_LINK_MANAGE,
    )
    service = ChatroomService(db)
    room = await service.get(chatroom_id)
    base = f"{request.url.scheme}://{request.url.netloc}"
    return GuestLinkOut(
        url=f"{base}/g/{room.id}/{room.guest_token}",
        chatroom_id=room.id,
        guest_token=room.guest_token,
    )


# --------------------------------------------------------------------------- #
# /compact slash command — G.10
# --------------------------------------------------------------------------- #


@chatroom_router.post(
    "/{chatroom_id}/compact",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Force context compaction for active agents in this room (G.10)",
)
async def compact_chatroom(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    """Trigger an immediate compaction pass for the room.

    Records a one-shot intent flag (K.2): the next agent turn in this room
    reads + clears it and forces a compaction pass before its provider call
    (``turn_engine._consume_compact_flag``). Returns 202 so the frontend slash
    command completes immediately.
    """
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    await _require_project_cap(db, principal, project_id, Capability.CHAT_SEND)
    from shared_kernel.auth.clients import get_redis

    await get_redis().set(f"compact:pending:{chatroom_id}", "1", ex=3600)
    return {"status": "accepted", "chatroom_id": str(chatroom_id)}


__all__ = ["chatroom_router", "workspace_router"]
