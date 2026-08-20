"""`visible_room_ids` must agree with `ensure_can_read`, room for room (R13.32).

The listing endpoints and the room-open path are two readers of one rule. If they
ever disagree, a room becomes either listable-but-unopenable (noise) or
openable-but-hidden (a room nobody can find). This module pins the agreement over
the full flag/role matrix rather than trusting that both call the same predicate,
because "both call the same predicate" is exactly the property a future
refactor breaks.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.conversation.application import access as access_mod
from contexts.conversation.application.access import (
    RoomAccess,
    ensure_can_read,
    visible_room_ids,
)
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.domain.models import Chatroom
from shared_kernel.auth.permissions import Role

_PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")

# Every role set the tenancy resolver can produce for a project scope, plus the
# empty set (a pure guest, or a stranger).
_ROLE_SETS: list[frozenset[Role]] = [
    frozenset(),
    frozenset({Role.ORG_MEMBER}),
    frozenset({Role.PROJECT_MEMBER}),
    frozenset({Role.ORG_MEMBER, Role.PROJECT_MEMBER}),
    frozenset({Role.PROJECT_OWNER}),
    frozenset({Role.ORG_OWNER, Role.PROJECT_OWNER}),
]


def _room(flags: tuple[bool, bool, bool, bool, bool]) -> Chatroom:
    org, project, owners_only, guests, member_groups = flags
    return Chatroom(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="room",
        allow_org_members=org,
        allow_project_members=project,
        allow_project_owners_only=owners_only,
        allow_guest_links=guests,
        allow_member_groups=member_groups,
        guest_token="t",
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


_GROUP_ID = uuid.UUID("77777777-7777-7777-7777-777777777777")


def _principal(*, is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user_id=_USER_ID, is_admin=is_admin, email_verified=True)


def _can_read(
    room: Chatroom, roles: frozenset[Role], *, is_guest: bool, in_bound_group: bool = False
) -> bool:
    """The single-room verdict, expressed through the public gate."""
    access = RoomAccess(
        chatroom=room,
        project_id=_PROJECT_ID,
        roles=roles,
        is_guest=is_guest,
        in_bound_group=in_bound_group,
    )
    try:
        ensure_can_read(access, is_admin=False)
    except ForbiddenInRoom:
        return False
    return True


async def _run(
    rooms: list[Chatroom],
    *,
    roles: frozenset[Role],
    guest_room_ids: set[uuid.UUID],
    in_bound_group: bool = False,
    is_admin: bool = False,
) -> set[uuid.UUID]:
    resolver = SimpleNamespace(roles_for=AsyncMock(return_value=roles))
    guests = SimpleNamespace(guest_room_ids=AsyncMock(return_value=guest_room_ids))
    bindings = SimpleNamespace(
        bound_group_ids=AsyncMock(
            return_value={room.id: {_GROUP_ID} for room in rooms if room.allow_member_groups}
        )
    )
    tenancy = SimpleNamespace(
        member_group_ids_for_user=AsyncMock(return_value={_GROUP_ID} if in_bound_group else set())
    )
    with (
        patch.object(access_mod, "TenancyRoleResolver", return_value=resolver),
        patch.object(access_mod, "ChatroomGuestRepository", return_value=guests),
        patch.object(access_mod, "ChatroomMemberGroupRepository", return_value=bindings),
        patch.object(access_mod, "TenancyFacade", return_value=tenancy),
    ):
        return await visible_room_ids(
            AsyncMock(),
            principal=_principal(is_admin=is_admin),
            rooms=[(_PROJECT_ID, room) for room in rooms],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", _ROLE_SETS, ids=lambda r: "+".join(sorted(x.value for x in r)) or "none")
@pytest.mark.parametrize("is_guest", [False, True], ids=["not_guest", "guest"])
@pytest.mark.parametrize("in_group", [False, True], ids=["not_in_group", "in_group"])
async def test_agrees_with_ensure_can_read_over_the_full_flag_matrix(
    roles: frozenset[Role], is_guest: bool, in_group: bool
) -> None:
    """All 32 flag combinations crossed with every role set, guest bit and group bit.

    This is the property the whole design rests on: the listing and the open path
    must never disagree about a room, for any principal.
    """
    rooms = [_room(flags) for flags in itertools.product([False, True], repeat=5)]
    guest_ids = {room.id for room in rooms} if is_guest else set()

    visible = await _run(rooms, roles=roles, guest_room_ids=guest_ids, in_bound_group=in_group)

    expected = {
        room.id
        for room in rooms
        if _can_read(
            room,
            roles,
            is_guest=is_guest,
            in_bound_group=in_group and room.allow_member_groups,
        )
    }
    assert visible == expected, (
        f"listing and read gate disagree for roles={sorted(r.value for r in roles)} "
        f"is_guest={is_guest} in_group={in_group}"
    )


@pytest.mark.asyncio
async def test_a_room_without_the_group_tier_is_never_asked_about_groups() -> None:
    """A project using no groups pays nothing for the feature existing."""
    rooms = [_room((False, True, False, False, False)) for _ in range(3)]
    bindings = SimpleNamespace(bound_group_ids=AsyncMock(return_value={}))
    tenancy = SimpleNamespace(member_group_ids_for_user=AsyncMock(return_value=set()))
    with (
        patch.object(
            access_mod,
            "TenancyRoleResolver",
            return_value=SimpleNamespace(roles_for=AsyncMock(return_value=frozenset({Role.PROJECT_MEMBER}))),
        ),
        patch.object(
            access_mod,
            "ChatroomGuestRepository",
            return_value=SimpleNamespace(guest_room_ids=AsyncMock(return_value=set())),
        ),
        patch.object(access_mod, "ChatroomMemberGroupRepository", return_value=bindings),
        patch.object(access_mod, "TenancyFacade", return_value=tenancy),
    ):
        visible = await visible_room_ids(
            AsyncMock(),
            principal=_principal(),
            rooms=[(_PROJECT_ID, room) for room in rooms],
        )

    assert visible == {room.id for room in rooms}
    bindings.bound_group_ids.assert_not_called()
    tenancy.member_group_ids_for_user.assert_not_called()


@pytest.mark.asyncio
async def test_an_owners_only_room_is_not_widened_by_a_group_binding() -> None:
    """SEC: `allow_project_owners_only` is exclusive, and the group tier sits
    inside its early return. A stale binding on such a room grants nothing."""
    room = _room((False, False, True, False, True))

    visible = await _run(
        [room],
        roles=frozenset({Role.PROJECT_MEMBER}),
        guest_room_ids=set(),
        in_bound_group=True,
    )

    assert visible == set()


@pytest.mark.asyncio
async def test_admin_sees_every_room_without_resolving_anything() -> None:
    rooms = [_room((False, False, True, False, False)) for _ in range(3)]
    resolver = SimpleNamespace(roles_for=AsyncMock())
    guests = SimpleNamespace(guest_room_ids=AsyncMock())
    with (
        patch.object(access_mod, "TenancyRoleResolver", return_value=resolver),
        patch.object(access_mod, "ChatroomGuestRepository", return_value=guests),
    ):
        visible = await visible_room_ids(
            AsyncMock(),
            principal=_principal(is_admin=True),
            rooms=[(_PROJECT_ID, room) for room in rooms],
        )
    assert visible == {room.id for room in rooms}
    resolver.roles_for.assert_not_called()
    guests.guest_room_ids.assert_not_called()


@pytest.mark.asyncio
async def test_empty_input_short_circuits() -> None:
    resolver = SimpleNamespace(roles_for=AsyncMock())
    guests = SimpleNamespace(guest_room_ids=AsyncMock())
    with (
        patch.object(access_mod, "TenancyRoleResolver", return_value=resolver),
        patch.object(access_mod, "ChatroomGuestRepository", return_value=guests),
    ):
        assert await visible_room_ids(AsyncMock(), principal=_principal(), rooms=[]) == set()
    resolver.roles_for.assert_not_called()
    guests.guest_room_ids.assert_not_called()


@pytest.mark.asyncio
async def test_roles_resolved_once_per_project_not_once_per_room() -> None:
    """A workspace of N rooms must not cost N role lookups."""
    rooms = [_room((False, True, False, False, False)) for _ in range(5)]
    resolver = SimpleNamespace(roles_for=AsyncMock(return_value=frozenset({Role.PROJECT_MEMBER})))
    guests = SimpleNamespace(guest_room_ids=AsyncMock(return_value=set()))
    with (
        patch.object(access_mod, "TenancyRoleResolver", return_value=resolver),
        patch.object(access_mod, "ChatroomGuestRepository", return_value=guests),
    ):
        await visible_room_ids(
            AsyncMock(),
            principal=_principal(),
            rooms=[(_PROJECT_ID, room) for room in rooms],
        )
    assert resolver.roles_for.await_count == 1
    assert guests.guest_room_ids.await_count == 1


@pytest.mark.asyncio
async def test_distinct_projects_are_resolved_separately() -> None:
    """Cross-project listings must not reuse one project's role set for another."""
    other_project = uuid.uuid4()
    open_room = _room((False, True, False, False, False))
    closed_room = _room((False, True, False, False, False))

    async def _roles_for(_principal_arg: object, scope: object) -> frozenset[Role]:
        project_id = getattr(scope, "project_id", None)
        return frozenset({Role.PROJECT_MEMBER}) if project_id == _PROJECT_ID else frozenset()

    resolver = SimpleNamespace(roles_for=AsyncMock(side_effect=_roles_for))
    guests = SimpleNamespace(guest_room_ids=AsyncMock(return_value=set()))
    with (
        patch.object(access_mod, "TenancyRoleResolver", return_value=resolver),
        patch.object(access_mod, "ChatroomGuestRepository", return_value=guests),
    ):
        visible = await visible_room_ids(
            AsyncMock(),
            principal=_principal(),
            rooms=[(_PROJECT_ID, open_room), (other_project, closed_room)],
        )
    assert visible == {open_room.id}
    assert resolver.roles_for.await_count == 2
