"""Removing, banning and unbanning a guest, and rotating the link -- AC-5, AC-6, AC-1 (event).

Spec: ``docs/tasks/2026-10-05-guest-kick-and-ban/spec.md`` §6, Q-3.

Row 18 (guest_link.manage) gates all four routes on the room's resolved roles.
The removal's room frames carry ids only and are sent only when something
changed, after commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.v1.chatrooms as chatrooms_route
from contexts.conversation.application.access import RoomAccess, ensure_can_manage_guest_link
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.interfaces import room_channel
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.permissions import Principal, Role
from tests.unit.chatroom_fakes import chatroom_row

_MEMBER = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)
_ADMIN = Principal(user_id=uuid.uuid4(), is_admin=True, email_verified=True)


def _access(*roles: Role, is_guest: bool = False) -> RoomAccess:
    return RoomAccess(
        chatroom=chatroom_row(), project_id=uuid.uuid4(), roles=frozenset(roles), is_guest=is_guest
    )


# -- row 18 --


@pytest.mark.parametrize("role", [Role.PROJECT_OWNER, Role.ORG_OWNER])
def test_owners_may_manage_guests(role: Role) -> None:
    ensure_can_manage_guest_link(_access(role), principal=_MEMBER)


def test_an_admin_with_no_room_role_may_manage_guests() -> None:
    ensure_can_manage_guest_link(_access(), principal=_ADMIN)


@pytest.mark.parametrize(
    "access",
    [
        pytest.param(_access(Role.PROJECT_MEMBER), id="project-member"),
        pytest.param(_access(Role.ORG_MEMBER), id="org-member"),
        pytest.param(_access(is_guest=True), id="guest"),
        pytest.param(_access(), id="no-role"),
    ],
)
def test_everyone_else_is_refused(access: RoomAccess) -> None:
    with pytest.raises(ForbiddenInRoom):
        ensure_can_manage_guest_link(access, principal=_MEMBER)


# -- routes --


def _publisher() -> tuple[MagicMock, list[tuple[str, str, dict[str, str]]]]:
    sent: list[tuple[str, str, dict[str, str]]] = []

    def _make(channel: str) -> SimpleNamespace:
        async def _emit(event: str, payload: dict[str, str]) -> None:
            sent.append((channel, event, payload))

        return SimpleNamespace(emit=_emit)

    return MagicMock(side_effect=_make), sent


def _resolving(access: RoomAccess) -> Any:
    async def resolve(_db: object, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        return access

    return resolve


def _removal(*session_ids: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(removed_session_ids=tuple(session_ids), changed=bool(session_ids))


def _facade() -> SimpleNamespace:
    return SimpleNamespace(
        remove_guest=AsyncMock(return_value=_removal(uuid.uuid4())),
        list_guest_bans=AsyncMock(return_value=[]),
        unban_guest=AsyncMock(),
        rotate_guest_link=AsyncMock(),
    )


def _request() -> MagicMock:
    request = MagicMock()
    request.url.scheme, request.url.netloc = "https", "smap.test"
    return request


async def _call_each_route(chatroom_id: uuid.UUID, db: Any) -> None:
    ctx = RequestContext()
    with pytest.raises(ForbiddenInRoom):
        await chatrooms_route.remove_guest(
            body=chatrooms_route.GuestRemoveIn(ban=True),
            chatroom_id=chatroom_id,
            guest_session_id=uuid.uuid4(),
            ctx=ctx,
            principal=_MEMBER,
            db=db,
        )
    with pytest.raises(ForbiddenInRoom):
        await chatrooms_route.list_guest_bans(chatroom_id=chatroom_id, principal=_MEMBER, db=db)
    with pytest.raises(ForbiddenInRoom):
        await chatrooms_route.unban_guest(
            chatroom_id=chatroom_id, ban_id=uuid.uuid4(), ctx=ctx, principal=_MEMBER, db=db
        )
    with pytest.raises(ForbiddenInRoom):
        await chatrooms_route.rotate_guest_link(
            request=_request(), chatroom_id=chatroom_id, ctx=ctx, principal=_MEMBER, db=db
        )
    with pytest.raises(ForbiddenInRoom):
        await chatrooms_route.read_guest_link(
            request=_request(), chatroom_id=chatroom_id, principal=_MEMBER, db=db
        )


async def test_every_route_refuses_a_project_member_before_acting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chatrooms_route, "resolve_room_access", _resolving(_access(Role.PROJECT_MEMBER)))
    facade = _facade()
    with patch.object(chatrooms_route, "ConversationFacade", return_value=facade):
        await _call_each_route(uuid.uuid4(), MagicMock(commit=AsyncMock()))
    for method in vars(facade).values():
        method.assert_not_awaited()


async def test_reading_the_link_uses_the_same_room_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Code review: reading and rotating the link answer to one row-18 gate."""
    room = chatroom_row()
    room.guest_token = "the-link-token"
    access = RoomAccess(
        chatroom=room, project_id=uuid.uuid4(), roles=frozenset({Role.ORG_OWNER}), is_guest=False
    )
    monkeypatch.setattr(chatrooms_route, "resolve_room_access", _resolving(access))
    out = await chatrooms_route.read_guest_link(
        request=_request(), chatroom_id=access.chatroom.id, principal=_MEMBER, db=MagicMock()
    )
    assert out.url == f"https://smap.test/g/{access.chatroom.id}/{access.chatroom.guest_token}"


@pytest.mark.parametrize("ended", [0, 1, 2])
async def test_removal_announces_each_ended_session_by_id_after_commit(
    monkeypatch: pytest.MonkeyPatch, ended: int
) -> None:
    """One frame per ended session (a ban ends the browser's other live sessions
    too), none when nothing ended, and no roster frame: the roster is unchanged."""
    monkeypatch.setattr(chatrooms_route, "resolve_room_access", _resolving(_access(Role.PROJECT_OWNER)))
    room = uuid.uuid4()
    session_ids = [uuid.uuid4() for _ in range(ended)]
    facade = _facade()
    facade.remove_guest = AsyncMock(return_value=_removal(*session_ids))
    publisher, sent = _publisher()
    order: list[str] = []
    db = MagicMock(commit=AsyncMock(side_effect=lambda: order.append("commit")))

    with (
        patch.object(chatrooms_route, "ConversationFacade", return_value=facade),
        patch.object(chatrooms_route, "Publisher", publisher),
    ):
        await chatrooms_route.remove_guest(
            body=chatrooms_route.GuestRemoveIn(ban=True),
            chatroom_id=room,
            guest_session_id=uuid.uuid4(),
            ctx=RequestContext(),
            principal=_MEMBER,
            db=db,
        )

    assert facade.remove_guest.await_args.kwargs["ban"] is True
    assert facade.remove_guest.await_args.kwargs["actor_user_id"] == _MEMBER.user_id
    assert sent == [
        (
            room_channel(room),
            "chatroom.guest_removed",
            {"chatroom_id": str(room), "guest_session_id": str(sid)},
        )
        for sid in session_ids
    ]
    assert order == (["commit"] if ended else [])


async def test_a_failed_emit_does_not_fail_the_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chatrooms_route, "resolve_room_access", _resolving(_access(Role.ORG_OWNER)))
    broken = SimpleNamespace(emit=AsyncMock(side_effect=RuntimeError("redis down")))
    facade = _facade()
    facade.remove_guest = AsyncMock(return_value=_removal(uuid.uuid4(), uuid.uuid4()))
    with (
        patch.object(chatrooms_route, "ConversationFacade", return_value=facade),
        patch.object(chatrooms_route, "Publisher", return_value=broken),
    ):
        await chatrooms_route.remove_guest(
            body=chatrooms_route.GuestRemoveIn(),
            chatroom_id=uuid.uuid4(),
            guest_session_id=uuid.uuid4(),
            ctx=RequestContext(),
            principal=_MEMBER,
            db=MagicMock(commit=AsyncMock()),
        )
    assert broken.emit.await_count == 2


async def test_the_ban_list_carries_name_and_time_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chatrooms_route, "resolve_room_access", _resolving(_access(Role.PROJECT_OWNER)))
    ban = SimpleNamespace(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        guest_session_id=uuid.uuid4(),
        display_name="Alice",
        created_by=uuid.uuid4(),
        created_at=datetime(2026, 10, 6, tzinfo=UTC),
    )
    facade = _facade()
    facade.list_guest_bans = AsyncMock(return_value=[ban])
    with patch.object(chatrooms_route, "ConversationFacade", return_value=facade):
        out = await chatrooms_route.list_guest_bans(
            chatroom_id=uuid.uuid4(), principal=_MEMBER, db=MagicMock()
        )
    assert [o.model_dump() for o in out] == [
        {"id": ban.id, "display_name": "Alice", "created_at": ban.created_at}
    ]


async def test_rotation_returns_the_new_link_and_signals_no_one_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """Code review: nothing in a viewer's room DTO changes, so a room frame would
    only tell guests that an invisible write happened."""
    monkeypatch.setattr(chatrooms_route, "resolve_room_access", _resolving(_access(Role.PROJECT_OWNER)))
    room = chatroom_row()
    rotated = SimpleNamespace(
        id=room.id, guest_token="fresh-token", created_by_user_id=room.created_by_user_id
    )
    facade = _facade()
    facade.rotate_guest_link = AsyncMock(return_value=rotated)
    emitted = AsyncMock()
    publisher, sent = _publisher()

    with (
        patch.object(chatrooms_route, "ConversationFacade", return_value=facade),
        patch.object(chatrooms_route, "_emit_chatroom_updated", emitted),
        patch.object(chatrooms_route, "Publisher", publisher),
    ):
        out = await chatrooms_route.rotate_guest_link(
            request=_request(), chatroom_id=room.id, ctx=RequestContext(), principal=_MEMBER, db=MagicMock()
        )

    assert out.url == f"https://smap.test/g/{room.id}/fresh-token"
    assert out.guest_token == "fresh-token"
    emitted.assert_not_awaited()
    assert sent == []
