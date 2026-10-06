"""Other viewers learn of a guest joining or renaming -- AC-4.

Spec: ``docs/tasks/2026-10-05-guest-room-read-and-identity/spec.md`` §7.3, Q-3.

The rename route emitted nothing and no event invalidated the roster, so a guest's
name never reached anyone else's screen without a reload. The fix emits an ids-only
``chatroom.members_changed`` on the room channel after commit -- and only when the
roster actually changed, because the session route is public and an unconditional
emit would let anyone holding the link flood the room.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response

import app.api.v1.guests as guests_route
from contexts.conversation.application.access import RoomAccess
from contexts.conversation.domain.errors import ForbiddenInRoom, GuestTokenInvalid
from contexts.conversation.interfaces import room_channel
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.permissions import Principal
from tests.unit.chatroom_fakes import chatroom_row

_EVENT = "chatroom.members_changed"


@pytest.fixture(autouse=True)
def _room(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The rename route re-checks room access; the room admits guests unless a
    test turns its guest links off."""
    room = chatroom_row()

    async def _resolve(db: object, *, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
        return RoomAccess(chatroom=room, project_id=uuid.uuid4(), roles=frozenset(), is_guest=True)

    monkeypatch.setattr(guests_route, "resolve_room_access", _resolve)
    return room


def _publisher() -> tuple[MagicMock, list[tuple[str, str, dict[str, str]]]]:
    sent: list[tuple[str, str, dict[str, str]]] = []

    def _make(channel: str) -> SimpleNamespace:
        async def _emit(event: str, payload: dict[str, str]) -> None:
            sent.append((channel, event, payload))

        return SimpleNamespace(emit=_emit)

    return MagicMock(side_effect=_make), sent


def _session_result(*, roster_changed: bool) -> SimpleNamespace:
    return SimpleNamespace(
        access_token="jwt",
        refresh_token="refresh",
        guest_session_id=uuid.uuid4(),
        display_name="Alice",
        is_resuming=not roster_changed,
        roster_changed=roster_changed,
    )


@pytest.mark.parametrize("changed", [True, False])
async def test_joining_emits_only_when_the_roster_changed(changed: bool) -> None:
    room = uuid.uuid4()
    db = MagicMock(commit=AsyncMock())
    facade = SimpleNamespace(
        create_or_resume_guest_session=AsyncMock(return_value=_session_result(roster_changed=changed))
    )
    publisher, sent = _publisher()

    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch.object(guests_route, "Publisher", publisher),
    ):
        await guests_route.create_guest_session(
            body=guests_route.GuestSessionIn(display_name="Alice"),
            response=Response(),
            chatroom_id=room,
            guest_token="t" * 32,
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            db=db,
        )

    expected = [(room_channel(room), _EVENT, {"chatroom_id": str(room)})] if changed else []
    assert sent == expected
    if changed:
        db.commit.assert_awaited()


@pytest.mark.parametrize("written", [True, False])
async def test_registered_enrolment_emits_only_when_a_row_was_written(written: bool) -> None:
    """docs/tasks/2026-10-05-guest-sender-marking code review: a registered guest's
    kind reaches open clients only with the roster re-read, so without this event
    their messages arrived unbadged until a reconnect ([R13.33])."""
    room = uuid.uuid4()
    db = MagicMock(commit=AsyncMock())
    service = SimpleNamespace(enroll=AsyncMock(return_value=written))
    publisher, sent = _publisher()

    with (
        patch.object(guests_route, "GuestService", return_value=service),
        patch.object(guests_route, "Publisher", publisher),
    ):
        await guests_route.enroll_guest(
            chatroom_id=room,
            guest_token="t" * 32,
            body=guests_route.GuestEnrollIn(display_name="Olive"),
            ctx=SimpleNamespace(actor_ip=None, request_id=None),
            principal=Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
            db=db,
        )

    expected = [(room_channel(room), _EVENT, {"chatroom_id": str(room)})] if written else []
    assert sent == expected


@pytest.mark.parametrize("changed", [True, False])
async def test_renaming_returns_the_stored_name_and_emits_only_on_a_change(changed: bool) -> None:
    room, session_id = uuid.uuid4(), uuid.uuid4()
    principal = Principal(
        user_id=session_id, is_admin=False, email_verified=False, is_guest=True, chatroom_id=room
    )
    db = MagicMock(commit=AsyncMock())
    facade = SimpleNamespace(
        update_guest_display_name=AsyncMock(
            return_value=SimpleNamespace(display_name="Alice Chen", changed=changed)
        )
    )
    publisher, sent = _publisher()

    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch.object(guests_route, "Publisher", publisher),
    ):
        out = await guests_route.update_guest_display_name(
            body=guests_route.GuestDisplayNameIn(display_name="  Alice   Chen "),
            guest_session_id=session_id,
            ctx=RequestContext(),
            principal=principal,
            db=db,
        )

    assert out.display_name == "Alice Chen"
    expected = [(room_channel(room), _EVENT, {"chatroom_id": str(room)})] if changed else []
    assert sent == expected


async def test_a_failed_emit_does_not_fail_the_rename() -> None:
    """The write is durable before the emit; a 500 for a change that happened is
    a worse lie than a missed refresh (the posture of `_emit_chatroom_updated`)."""
    room, session_id = uuid.uuid4(), uuid.uuid4()
    principal = Principal(
        user_id=session_id, is_admin=False, email_verified=False, is_guest=True, chatroom_id=room
    )
    facade = SimpleNamespace(
        update_guest_display_name=AsyncMock(return_value=SimpleNamespace(display_name="Bob", changed=True))
    )
    broken = SimpleNamespace(emit=AsyncMock(side_effect=RuntimeError("redis down")))

    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch.object(guests_route, "Publisher", return_value=broken),
    ):
        out = await guests_route.update_guest_display_name(
            body=guests_route.GuestDisplayNameIn(display_name="Bob"),
            guest_session_id=session_id,
            ctx=RequestContext(),
            principal=principal,
            db=MagicMock(commit=AsyncMock()),
        )

    assert out.display_name == "Bob"


def _rename_facade() -> SimpleNamespace:
    return SimpleNamespace(
        update_guest_display_name=AsyncMock(return_value=SimpleNamespace(display_name="Bob", changed=True))
    )


async def test_a_guest_the_room_no_longer_admits_cannot_rename(_room: SimpleNamespace) -> None:
    """The access token outlives guest links being turned off."""
    _room.allow_guest_links = False
    room, session_id = uuid.uuid4(), uuid.uuid4()
    principal = Principal(
        user_id=session_id, is_admin=False, email_verified=False, is_guest=True, chatroom_id=room
    )
    facade = _rename_facade()
    publisher, sent = _publisher()

    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch.object(guests_route, "Publisher", publisher),
        pytest.raises(ForbiddenInRoom),
    ):
        await guests_route.update_guest_display_name(
            body=guests_route.GuestDisplayNameIn(display_name="Bob"),
            guest_session_id=session_id,
            ctx=RequestContext(),
            principal=principal,
            db=MagicMock(commit=AsyncMock()),
        )

    facade.update_guest_display_name.assert_not_awaited()
    assert sent == []


@pytest.mark.parametrize("case", ["other_session", "no_room", "registered_user"])
async def test_only_the_session_itself_may_rename(case: str) -> None:
    session_id = uuid.uuid4()
    principal = Principal(
        user_id=uuid.uuid4() if case == "other_session" else session_id,
        is_admin=False,
        email_verified=case == "registered_user",
        is_guest=case != "registered_user",
        chatroom_id=None if case == "no_room" else uuid.uuid4(),
    )
    facade = _rename_facade()

    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        pytest.raises(GuestTokenInvalid),
    ):
        await guests_route.update_guest_display_name(
            body=guests_route.GuestDisplayNameIn(display_name="Bob"),
            guest_session_id=session_id,
            ctx=RequestContext(),
            principal=principal,
            db=MagicMock(commit=AsyncMock()),
        )

    facade.update_guest_display_name.assert_not_awaited()
