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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    ForbiddenInRoom,
    NotRoomCreator,
    WorkspaceNotFound,
)
from contexts.conversation.domain.models import Chatroom, ExportSenderScope
from contexts.conversation.infrastructure.repositories import (
    ChatroomGuestRepository,
    ChatroomMemberGroupRepository,
    ChatroomRepository,
    WorkspaceRepository,
)
from contexts.tenancy.interfaces.facade import TenancyFacade
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import (
    Capability,
    Outcome,
    Principal,
    Role,
    RoleResolver,
    Scope,
    outcome_for,
)

_Row = TypeVar("_Row")


def is_moderator_roles(roles: frozenset[Role]) -> bool:
    """Moderator predicate over a resolved role set (R13.23, R5.03).

    Kept as a free function so the route that *serializes* the bit into
    `ChatroomOut.is_moderator` and the gates that *enforce* it read the same
    expression — a second, hand-inlined copy is how the two halves drift.
    Admin is handled outside via `principal.is_admin`.
    """
    return Role.PROJECT_OWNER in roles or Role.ORG_OWNER in roles


@dataclass(frozen=True, slots=True)
class RoomAccess:
    chatroom: Chatroom
    project_id: uuid.UUID
    roles: frozenset[Role]
    is_guest: bool
    # §13.2a. Carried the way `is_guest` is, and for the same reason: membership of
    # a Member Group bound to this room is a per-resource grant, not a role, so it
    # has no place in `roles` and no cell in the §5.2 matrix (R5.06, R13.30).
    # Defaulted so every existing construction site keeps meaning what it meant.
    in_bound_group: bool = False

    @property
    def can_read(self) -> bool:
        return bool(self.roles) or self.is_guest or self.in_bound_group

    @property
    def is_moderator(self) -> bool:
        return is_moderator_roles(self.roles)


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
        in_bound_group=await _in_bound_group(db, principal=principal, chatroom=chatroom),
    )


async def _in_bound_group(
    db: AsyncSession,
    *,
    principal: Principal,
    chatroom: Chatroom,
) -> bool:
    """Is the caller in any Member Group bound to this room? (§13.2a)

    Resolved here, per request, rather than cached on `RoomAccess` construction
    elsewhere — the chatroom WebSocket re-runs `resolve_room_access` on its
    mid-socket re-auth (`app/api/ws/chatroom.py`), so removing someone from a
    group drops their live socket at the next window rather than at their next
    reconnect. Anything that memoises this across a socket's lifetime silently
    breaks revocation.

    Short-circuits when the room does not have the tier on: an unbound or
    tier-off room asks tenancy nothing.

    Deleted groups: the bindings are not filtered, the caller's group ids are
    (`MemberGroupRepository.group_ids_for_user` reads live rows only), so a
    binding to a soft-deleted group can never intersect and grants nothing —
    R13.29, enforced by the shape of the intersection rather than by a check that
    could be forgotten.
    """
    if not chatroom.allow_member_groups:
        return False
    bound = await ChatroomMemberGroupRepository(db).list_for_room(chatroom.id)
    if not bound:
        return False
    mine = await TenancyFacade(db).member_group_ids_for_user(principal.user_id)
    return bool(bound & mine)


def _satisfies_room_flags(access: RoomAccess) -> bool:
    """Does the caller satisfy at least one enabled §21.1 access tier?

    The single authoritative room-membership matrix, shared by read and send so
    the privacy flags gate *confidentiality*, not just sending:

      - allow_project_owners_only — ONLY project/org owners (exclusive: when set,
        no other flag widens access).
      - allow_project_members     — project owners + project members.
      - allow_member_groups       — members of a Member Group bound to this room.
      - allow_org_members         — org owners + org members.
      - allow_guest_links         — users on chatroom_guests.

    Moderators (project/org owner, explicit or org-inherited per R5.03) sit in
    the most permissive tier and clear every subset.

    SEC: the member-group tier sits *inside* the `allow_project_owners_only`
    early return. Outside it, an owners-only room carrying a stale binding would
    become group-readable — the exclusivity of that flag is the one property no
    other tier may widen.
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
    if room.allow_member_groups and access.in_bound_group:
        return True
    if room.allow_org_members and (Role.ORG_MEMBER in access.roles or Role.ORG_OWNER in access.roles):
        return True
    return bool(room.allow_guest_links and access.is_guest)


async def visible_room_ids(
    db: AsyncSession,
    *,
    principal: Principal,
    rooms: Sequence[tuple[uuid.UUID, Chatroom]],
) -> set[uuid.UUID]:
    """Which of these `(project_id, room)` pairs may `principal` read? (R13.32)

    The batch form of `ensure_can_read`, for the three listing endpoints. It
    resolves the same inputs `resolve_room_access` resolves — roles per project,
    guest membership per room — but once per distinct project and once for the
    whole room set, then runs each room through `_satisfies_room_flags`.

    SEC: it deliberately calls that same private predicate rather than restating
    the tier logic, because enumeration and access must agree by construction.
    A second copy — in Python or in SQL — is how a room becomes listable but
    unopenable, or worse, the reverse. `tests/unit/test_visible_room_ids.py`
    pins the agreement over the full flag/role matrix.

    Rooms are not deduplicated and the caller's order is not preserved: the
    return value is a membership set, and callers filter their own ordered list
    through it.
    """
    if principal.is_admin:
        return {room.id for _, room in rooms}
    if not rooms:
        return set()

    # Resolved once per project, not once per room. The role resolver answers a
    # project/org question and ignores `Scope.chatroom_id` by design — its
    # `is_chatroom_participant` refuses outright and points callers here for the
    # room ACL (`tenancy/interfaces/role_resolver.py:74-86`). So the scope passed
    # here omits the room rather than passing one room's id and reusing the answer
    # for its siblings, which would read as a per-room result that it is not.
    resolver = TenancyRoleResolver(db)
    roles_by_project: dict[uuid.UUID, frozenset[Role]] = {}
    for project_id, _room in rooms:
        if project_id not in roles_by_project:
            roles_by_project[project_id] = await resolver.roles_for(
                principal,
                Scope(project_id=project_id),
            )

    guest_ids = await ChatroomGuestRepository(db).guest_room_ids(
        user_id=principal.user_id,
        chatroom_ids=[room.id for _, room in rooms],
    )

    # §13.2a, batched: the caller's live group ids once, the bindings of the rooms
    # that actually have the tier on once. A room whose tier is off is not asked
    # about, so a project using no groups pays nothing for the feature existing.
    grouped_room_ids = [room.id for _, room in rooms if room.allow_member_groups]
    bindings: dict[uuid.UUID, set[uuid.UUID]] = {}
    my_group_ids: set[uuid.UUID] = set()
    if grouped_room_ids:
        bindings = await ChatroomMemberGroupRepository(db).bound_group_ids(grouped_room_ids)
        if bindings:
            my_group_ids = await TenancyFacade(db).member_group_ids_for_user(principal.user_id)

    visible: set[uuid.UUID] = set()
    for project_id, room in rooms:
        access = RoomAccess(
            chatroom=room,
            project_id=project_id,
            roles=roles_by_project[project_id],
            is_guest=room.id in guest_ids,
            in_bound_group=bool(bindings.get(room.id, frozenset()) & my_group_ids),
        )
        if _satisfies_room_flags(access):
            visible.add(room.id)
    return visible


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


async def _room_readable(
    db: AsyncSession,
    *,
    principal: Principal,
    chatroom_id: uuid.UUID,
) -> bool:
    """`ensure_can_read` as a boolean, for callers that filter rather than raise.

    SEC: fails closed on a room that cannot be resolved. `resolve_room_access`
    raises when the room, its workspace or its project is gone or soft-deleted,
    and a record pointing at an unreachable room must deny — never fall through
    to a weaker check.

    Calls `_satisfies_room_flags` rather than catching `ensure_can_read`'s
    `ForbiddenInRoom`, for the reason `visible_room_ids` gives: one predicate,
    no second copy of the tier logic. Admin is the caller's business — both
    public entry points below bypass before reaching here.
    """
    try:
        access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    except (ChatroomNotFound, WorkspaceNotFound):
        return False
    return _satisfies_room_flags(access)


async def _is_project_backstage_reader(
    principal: Principal,
    *,
    project_id: uuid.UUID,
    resolver: RoleResolver,
) -> bool:
    return is_moderator_roles(await resolver.roles_for(principal, Scope(project_id=project_id)))


async def can_read_orchestration_record(
    db: AsyncSession,
    *,
    principal: Principal,
    chatroom_id: uuid.UUID | None,
    project_id: uuid.UUID,
    resolver: RoleResolver,
) -> bool:
    """May this principal read one orchestration record? (R15.24)

    Named for its caller rather than for this context, because the rule it
    encodes is about orchestration records and only the *evaluation* is the
    conversation context's: a record that names a chat room is readable by
    exactly the people who may read that room (§13.2), and a record that names
    none is backstage and follows [R14.10] — Admin and Project/Org Owners.

    A record whose room was deleted arrives here with `chatroom_id=None` (both
    FKs are `ON DELETE SET NULL`) and is therefore backstage, not
    project-readable: deleting a room must never widen who can read what ran
    inside it.

    Returns a verdict instead of raising so the caller owns the status code.
    That is not a style preference — the room branch must answer 404, byte for
    byte what a missing record answers, and only the route knows the resource
    name that goes in that body.

    `resolver` is consulted on the backstage branch only. The room branch goes
    through `resolve_room_access`, which builds its own `TenancyRoleResolver`
    because it needs a room-scoped `Scope` this signature does not carry — so a
    caller (or a test) that swaps `resolver` changes the backstage verdict and
    not the room one.
    """
    if principal.is_admin:
        return True
    if chatroom_id is None:
        return await _is_project_backstage_reader(principal, project_id=project_id, resolver=resolver)
    return await _room_readable(db, principal=principal, chatroom_id=chatroom_id)


async def filter_readable_by_room(
    db: AsyncSession,
    *,
    principal: Principal,
    rows: Sequence[_Row],
    chatroom_id_of: Callable[[_Row], uuid.UUID | None],
    project_id: uuid.UUID,
    resolver: RoleResolver,
) -> list[_Row]:
    """The listing form of `can_read_orchestration_record`, order-preserving.

    Rows the caller may not read are omitted rather than refused (R15.24), and
    the result discloses nothing about what was withheld — callers must filter
    *before* slicing a page, or the page length becomes the disclosure.

    Both lookups are memoised for the call: the room verdict per distinct room
    id, the backstage verdict once. A run's approvals almost always name one
    room, so this is one `resolve_room_access` for the page rather than one per
    row.
    """
    if principal.is_admin:
        return list(rows)

    backstage: bool | None = None
    by_room: dict[uuid.UUID, bool] = {}
    readable: list[_Row] = []
    for row in rows:
        room_id = chatroom_id_of(row)
        if room_id is None:
            if backstage is None:
                backstage = await _is_project_backstage_reader(
                    principal, project_id=project_id, resolver=resolver
                )
            if backstage:
                readable.append(row)
            continue
        if room_id not in by_room:
            by_room[room_id] = await _room_readable(db, principal=principal, chatroom_id=room_id)
        if by_room[room_id]:
            readable.append(row)
    return readable


def export_sender_scope(access: RoomAccess, *, principal: Principal) -> ExportSenderScope:
    """Matrix row 19 (chat.export) — how much of a readable room may the caller
    take away? Raises `ForbiddenInRoom` when the row grants them nothing.

    Decided semantics (dossier 2026-07-22-chat-export-authz-and-polling, Q-1a and
    Q-3): a narrowed export contains the caller's own messages plus all agent and
    system messages in the room; messages sent by *other users*, together with
    their edit histories and attachments, are excluded. Per Q-2, guests may not
    export at all.

    This is row 19's only interpreter, and it reads the row's cells through
    `outcome_for` rather than restating them, so the matrix stays authoritative
    (R5.05). `decide()` cannot serve this row: the tenancy resolver never emits
    `Role.GUEST`, so a `decide()`-based check would deny every guest by accident
    rather than by decision, and `Outcome.OWN_ONLY` there compares a stored
    resource owner, which an aggregate like an export does not have.

    Callers must still run `ensure_can_read` first. Row 19 narrows *within* a
    room the caller may read; it is not a substitute for the four-flag room gate.
    """
    if principal.is_admin:
        return ExportSenderScope.ALL
    outcomes = {outcome_for(Capability.CHAT_EXPORT, role) for role in access.roles}
    if Outcome.ALLOW in outcomes:
        return ExportSenderScope.ALL
    if Outcome.OWN_ONLY in outcomes:
        return ExportSenderScope.OWN_PLUS_NON_USER
    # Reached by pure guests: they hold no org/project role, so the row offers
    # them no cell at all.
    raise ForbiddenInRoom("caller cannot export this chatroom")


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
    "can_read_orchestration_record",
    "ensure_can_read",
    "ensure_can_send",
    "ensure_room_creator",
    "export_sender_scope",
    "filter_readable_by_room",
    "is_moderator_roles",
    "is_room_creator",
    "resolve_room_access",
    "visible_room_ids",
]
