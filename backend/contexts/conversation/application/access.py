"""Room-scoped ACL evaluation (§21.1 flags + R13.04).

Permission-matrix rows 17 (chat.send), 19 (chat.export), 20 (message.delete)
resolve as `ROOM_ACL` (§5.2). Matrix delegates to here so the four independent
boolean flags can gate access in one authoritative place.

SoC: this helper only decides "may this principal read/send here?" using the
tenancy role resolver (no cross-context FK joins) and the room's own flags.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    ForbiddenInRoom,
    WorkspaceNotFound,
)
from contexts.conversation.domain.models import Chatroom
from contexts.conversation.infrastructure.repositories import (
    ChatroomGuestRepository,
    ChatroomRepository,
    WorkspaceRepository,
)
from contexts.tenancy.interfaces.facade import TenancyFacade
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import Principal, Role, Scope


@dataclass(frozen=True, slots=True)
class RoomAccess:
    chatroom: Chatroom
    project_id: uuid.UUID
    roles: frozenset[Role]
    is_guest: bool

    @property
    def can_read(self) -> bool:
        return bool(self.roles) or self.is_guest

    @property
    def is_moderator(self) -> bool:
        # Admin is handled outside via `principal.is_admin`.
        return Role.PROJECT_OWNER in self.roles or Role.ORG_OWNER in self.roles


async def resolve_room_access(
    db: AsyncSession,
    *,
    principal: Principal,
    chatroom_id: uuid.UUID,
) -> RoomAccess:
    """Fetch the chatroom, resolve parent project, compute the caller's roles
    and guest flag. Raises `ChatroomNotFound` if the room is missing."""
    chatrooms = ChatroomRepository(db)
    workspaces = WorkspaceRepository(db)
    tenancy = TenancyFacade(db)
    guests = ChatroomGuestRepository(db)

    chatroom = await chatrooms.get(chatroom_id)
    if chatroom is None:
        raise ChatroomNotFound(str(chatroom_id))

    workspace = await workspaces.get(chatroom.workspace_id)
    if workspace is None:
        raise WorkspaceNotFound(str(chatroom.workspace_id))

    # Confirm the parent project exists and is not soft-deleted. If it is, the
    # room is effectively unreachable.
    project = await tenancy.get_project(workspace.project_id)
    if project is None:
        raise ChatroomNotFound(str(chatroom_id))

    resolver = TenancyRoleResolver(db)
    roles = await resolver.roles_for(
        principal,
        Scope(project_id=project.id, chatroom_id=chatroom_id),
    )
    is_guest = await guests.is_guest(
        chatroom_id=chatroom_id,
        user_id=principal.user_id,
    )
    return RoomAccess(
        chatroom=chatroom,
        project_id=project.id,
        roles=roles,
        is_guest=is_guest,
    )


def ensure_can_read(access: RoomAccess, *, is_admin: bool) -> None:
    """Membership gate. Any role or guest flag allows read."""
    if is_admin:
        return
    if not access.can_read:
        raise ForbiddenInRoom("caller is not a participant of this chatroom")


def ensure_can_send(access: RoomAccess, *, is_admin: bool) -> None:
    """Evaluate §21.1 flags against the caller's room-scoped roles (R13.04).

    Matrix row 17 already screens callers down to {org_*, project_*, guest}
    before this function is reached. We then intersect with the room flags:

      - allow_project_owners_only — ONLY project/org owners.
      - allow_project_members     — project owners + project members.
      - allow_org_members         — org owners + org members.
      - allow_guest_links         — users on chatroom_guests.

    Admin bypass fires first.
    """
    if is_admin:
        return
    room = access.chatroom
    # Project owners (explicit role or org-inherited) are always in the most
    # permissive tier and clear every flag subset.
    if access.is_moderator:
        return
    if room.allow_project_owners_only and not access.is_moderator:
        raise ForbiddenInRoom("room restricted to project owners")
    if room.allow_project_members and (
        Role.PROJECT_MEMBER in access.roles or Role.PROJECT_OWNER in access.roles
    ):
        return
    if room.allow_org_members and (Role.ORG_MEMBER in access.roles or Role.ORG_OWNER in access.roles):
        return
    if room.allow_guest_links and access.is_guest:
        return
    raise ForbiddenInRoom("caller cannot send in this chatroom")


__all__ = [
    "RoomAccess",
    "ensure_can_read",
    "ensure_can_send",
    "resolve_room_access",
]
