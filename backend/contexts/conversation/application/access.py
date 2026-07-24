"""Room-scoped ACL evaluation (§21.1 flags + R13.04).

Only permission-matrix row 17 (chat.send) resolves as `ROOM_ACL` (§5.2);
rows 19 (chat.export) and 20 (message.delete) resolve as `OWN_ONLY`. The matrix
delegates the ROOM_ACL row to here so the four independent boolean flags can gate
access in one authoritative place.

`ensure_can_read` / `ensure_can_send` are therefore *not* row-19 or row-20
enforcement: they answer "may this caller see this room", not "how much of it may
this caller take away". Row 19's narrowing lives in `export_sender_scope` below;
row 20's is inline at `app/api/v1/messages.py`.

SoC: these helpers only decide "may this principal read/send here?" using the
tenancy role resolver (no cross-context FK joins) and the room's own flags.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    ForbiddenInRoom,
    NotRoomCreator,
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


def _satisfies_room_flags(access: RoomAccess) -> bool:
    """Does the caller satisfy at least one enabled §21.1 access tier?

    The single authoritative room-membership matrix, shared by read and send so
    the four privacy flags gate *confidentiality*, not just sending:

      - allow_project_owners_only — ONLY project/org owners (exclusive: when set,
        no other flag widens access).
      - allow_project_members     — project owners + project members.
      - allow_org_members         — org owners + org members.
      - allow_guest_links         — users on chatroom_guests.

    Moderators (project/org owner, explicit or org-inherited per R5.03) sit in
    the most permissive tier and clear every subset.
    """
    room = access.chatroom
    if access.is_moderator:
        return True
    if room.allow_project_owners_only:
        return False
    if room.allow_project_members and (
        Role.PROJECT_MEMBER in access.roles or Role.PROJECT_OWNER in access.roles
    ):
        return True
    if room.allow_org_members and (Role.ORG_MEMBER in access.roles or Role.ORG_OWNER in access.roles):
        return True
    return bool(room.allow_guest_links and access.is_guest)


def ensure_can_read(access: RoomAccess, *, is_admin: bool) -> None:
    """Read gate (R13.04 + §21.1).

    SEC: read confidentiality mirrors the send matrix. Holding *any* role on the
    parent org/project is not sufficient — the caller must satisfy at least one
    enabled room access tier, otherwise an org member (or a guest whose link was
    revoked) could read an owners-only / project-only room. Admin bypasses.
    """
    if is_admin:
        return
    if not _satisfies_room_flags(access):
        raise ForbiddenInRoom("caller cannot read this chatroom")


def is_room_creator(access: RoomAccess, *, principal: Principal) -> bool:
    """R28.02 — who may see observer surfaces (observations, roles, disclosure).

    Creator when `created_by_user_id` matches AND the caller still holds a
    role in the project or its parent org (O-7: authority does not survive
    losing all membership — `access.roles` is the live project/org role set;
    an org-level role is retained deliberately, matching moderator semantics);
    legacy rooms (NULL creator, pre-0041 backfill miss) fall back to moderator
    semantics. Admin bypasses. Pure guests are never creators —
    `ensure_can_read` alone does NOT exclude them (guest links satisfy the
    read flags), so the explicit branch here is load-bearing.
    """
    if principal.is_admin:
        return True
    if access.is_guest and not access.roles:
        return False
    room = access.chatroom
    if room.created_by_user_id is not None:
        return bool(access.roles) and principal.user_id == room.created_by_user_id
    return access.is_moderator


def ensure_room_creator(access: RoomAccess, *, principal: Principal) -> None:
    if not is_room_creator(access, principal=principal):
        raise NotRoomCreator("caller is not this chatroom's creator")


def ensure_can_send(access: RoomAccess, *, is_admin: bool) -> None:
    """Evaluate §21.1 flags against the caller's room-scoped roles (R13.04).

    Matrix row 17 already screens callers down to {org_*, project_*, guest}
    before this function is reached. Admin bypass fires first.
    """
    if is_admin:
        return
    if not _satisfies_room_flags(access):
        raise ForbiddenInRoom("caller cannot send in this chatroom")


__all__ = [
    "RoomAccess",
    "ensure_can_read",
    "ensure_can_send",
    "ensure_room_creator",
    "is_room_creator",
    "resolve_room_access",
]
