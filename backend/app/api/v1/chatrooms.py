"""`/api/workspaces/{id}/chatrooms` + `/api/chatrooms/*` — F.2 / §22.10."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import PaginationParams
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.application.access import (
    ensure_room_creator,
    is_room_creator,
    resolve_room_access,
)
from contexts.conversation.application.chatroom_service import (
    ChatroomFlagsPatch,
    ChatroomService,
)
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    WorkspaceNotFound,
)
from contexts.conversation.domain.models import ChatroomAgentRole
from contexts.conversation.interfaces.author_labels import prefer_guest_label
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.identity.interfaces.facade import IdentityFacade
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
    # R28.09 — creator-only (per-field gate in the handler; the capability
    # check above it covers the other fields).
    disclose_observers: bool | None = None


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
    created_by_user_id: uuid.UUID | None
    disclose_observers: bool
    # "You are notified that observers are enabled" — false whenever
    # disclosure is off, regardless of actual bindings (R28.09).
    observers_present: bool


class GuestLinkOut(BaseModel):
    url: str
    chatroom_id: uuid.UUID
    guest_token: str


class AgentRef(BaseModel):
    agent_id: uuid.UUID
    # Response-side: populated only for the room creator (R28.10); the field
    # is dropped from serialization when None. Request-side (POST body):
    # optional, defaults to a normal binding.
    role: Literal["normal", "observer"] | None = None


class AgentRolePatchIn(BaseModel):
    role: Literal["normal", "observer"]


class ChatroomMemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str | None


def _to_out(r, *, has_observers: bool = False) -> ChatroomOut:
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
        created_by_user_id=r.created_by_user_id,
        disclose_observers=r.disclose_observers,
        observers_present=bool(r.disclose_observers and has_observers),
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
    pagination: PaginationParams = Depends(),
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
    rows = await service.list_for_workspace(
        workspace_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    with_observers = await service.rooms_with_observers([r.id for r in rows])
    return [_to_out(r, has_observers=r.id in with_observers) for r in rows]


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
    with_observers = await service.rooms_with_observers([chatroom_id])
    return _to_out(room, has_observers=chatroom_id in with_observers)


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
    if body.disclose_observers is not None:
        # R28.09: disclosure is the creator's call, not any RESOURCE_CREATE_EDIT
        # holder's — per-field gate on top of the capability check above.
        access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
        ensure_room_creator(access, principal=principal)
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
    with_observers = await service.rooms_with_observers([chatroom_id])
    return _to_out(room, has_observers=chatroom_id in with_observers)


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


@chatroom_router.get("/{chatroom_id}/agents", response_model_exclude_none=True)
async def list_chatroom_agents(
    chatroom_id: uuid.UUID = Path(...),
    pagination: PaginationParams = Depends(),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[AgentRef]:
    # Single fetch for both checks below: resolve_room_access already loads
    # the chatroom + workspace + project and resolves roles (chatroom_id in
    # its Scope is inert for role resolution — TenancyRoleResolver.roles_for
    # only reads org_id/project_id — so access.roles is exactly the project
    # membership set a separate `_project_id_for_chatroom` +
    # `roles_for(Scope(project_id=...))` call would have computed).
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    if not principal.is_admin and not access.roles:
        _raise_forbidden("not a member of the project")
    # R28.10: only the creator sees observer bindings (and roles at all) — for
    # everyone else the response is shape-identical to the pre-observer API.
    creator = is_room_creator(access, principal=principal)
    service = ChatroomService(db)
    rows = list(await service.list_agents(chatroom_id))
    if not creator:
        rows = [r for r in rows if r.role is ChatroomAgentRole.NORMAL]
    rows = rows[pagination.offset : pagination.offset + pagination.limit]
    return [AgentRef(agent_id=r.agent_id, role=r.role.value if creator else None) for r in rows]


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
    role = ChatroomAgentRole(body.role) if body.role else ChatroomAgentRole.NORMAL
    if role is ChatroomAgentRole.OBSERVER:
        # R28.02: only the creator may plant an observer — a non-creator
        # moderator would be binding an agent whose output they cannot read.
        access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
        ensure_room_creator(access, principal=principal)
    service = ChatroomService(db)
    await service.add_agent(
        chatroom_id=chatroom_id,
        agent_id=body.agent_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        role=role,
        request_id=ctx.request_id,
    )


@chatroom_router.patch(
    "/{chatroom_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def patch_chatroom_agent_role(
    body: AgentRolePatchIn,
    chatroom_id: uuid.UUID = Path(...),
    agent_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_room_creator(access, principal=principal)
    service = ChatroomService(db)
    changed = await service.set_agent_role(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        role=ChatroomAgentRole(body.role),
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if not changed:
        raise HTTPException(status_code=404, detail="agent is not bound to this chatroom")


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
# Members — human author roster for resolving chat-message display names
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}/members")
async def list_chatroom_members(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ChatroomMemberOut]:
    """Resolve human participants to display names so the client can label
    message authors (REST history + live WS messages share one map).

    Only ``user_id`` + ``display_name`` is returned — never email — so a room
    member (including a guest) cannot harvest other participants' login
    identifiers. The id set is the union of distinct human message authors and
    enrolled guests; a guest's per-room display name takes precedence over their
    account display name. Names left unset resolve to ``null`` and the client
    falls back to a short id.
    """
    project_id = await _project_id_for_chatroom(db, chatroom_id)
    conv = ConversationFacade(db)
    if not principal.is_admin:
        resolver = await get_role_resolver(db)
        roles = await resolver.roles_for(
            principal,
            Scope(project_id=project_id, chatroom_id=chatroom_id),
        )
        is_guest = await conv.is_chatroom_guest(
            chatroom_id=chatroom_id,
            user_id=principal.user_id,
        )
        if not roles and not is_guest:
            _raise_forbidden("not a participant of this room")
    guest_names = {g.user_id: g.display_name for g in await conv.list_guests(chatroom_id)}
    sender_ids = await conv.distinct_user_sender_ids(chatroom_id)
    all_ids = sender_ids | set(guest_names)
    account_names = await IdentityFacade(db).get_display_names(list(all_ids))

    return [
        ChatroomMemberOut(
            user_id=uid,
            display_name=prefer_guest_label(guest_names.get(uid), account_names.get(uid)),
        )
        for uid in all_ids
    ]


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
    service = ChatroomService(db)
    await service.request_compaction(chatroom_id)
    return {"status": "accepted", "chatroom_id": str(chatroom_id)}


# --------------------------------------------------------------------------- #
# Presence snapshot — FIX-05
# --------------------------------------------------------------------------- #


class PresenceOut(BaseModel):
    user_ids: list[str]


@chatroom_router.get(
    "/{chatroom_id}/presence",
    summary="Snapshot of users currently present via WebSocket",
)
async def get_chatroom_presence(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> PresenceOut:
    from contexts.conversation.application.access import (
        ensure_can_read,
        resolve_room_access,
    )
    from contexts.conversation.interfaces import PresenceTracker

    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    members = await PresenceTracker().list_room(chatroom_id)
    return PresenceOut(user_ids=[str(uid) for uid in members])


__all__ = ["chatroom_router", "workspace_router"]
