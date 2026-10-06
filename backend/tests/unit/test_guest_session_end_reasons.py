"""A guest learns why its session cannot continue -- F-18, F-20.

Spec: ``docs/tasks/2026-10-05-guest-frontend-session-lifecycle/spec.md`` §7.5, Q-4, Q-5.

``GuestTokenInvalid`` (404) used to answer a bad link, links turned off, a dead
cookie and an invisible-only name alike, so the client could only ever say "this
link is no longer valid". Links off now answers ``GuestAccessDisabled`` (403) and an
empty normalised name ``GuestDisplayNameInvalid`` (422) -- but only to a caller that
already proved it holds the link, a cookie matched to a session of the room, or a
guest token, so naming the reason discloses nothing to an outsider ([R13.32]).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.v1.guests as guests_route
from contexts.conversation.application.guest_service import GuestService
from contexts.conversation.application.guest_session_service import GuestSessionService
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    GuestAccessDisabled,
    GuestDisplayNameInvalid,
    GuestRemoved,
    GuestTokenInvalid,
)
from contexts.conversation.domain.models import GuestSession
from contexts.conversation.interfaces import error_mapping
from shared_kernel.auth.permissions import Principal

_SERVICE = "contexts.conversation.application.guest_session_service"
_ZERO_WIDTH = "\u200b\u200b"


def _room(chatroom_id: uuid.UUID, *, links: bool = True, token: str = "correct-token") -> MagicMock:
    room = MagicMock()
    room.id = chatroom_id
    room.guest_token = token
    room.allow_guest_links = links
    return room


def _account() -> Principal:
    return Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)


def _session(chatroom_id: uuid.UUID) -> GuestSession:
    return GuestSession(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        display_name="Alice",
        browser_id="br-1",
        refresh_token_hash="hash",
        last_seen_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def live_parents() -> Iterator[None]:
    """The room's workspace and project are live; test_guest_room_liveness covers the rest."""
    with patch(f"{_SERVICE}.ensure_parents_live", AsyncMock()):
        yield


@pytest.fixture
def service() -> GuestSessionService:
    return GuestSessionService(AsyncMock())


# -- invisible-only names (F-18, Q-5) --


async def test_create_rejects_a_name_that_normalises_to_nothing(service: GuestSessionService) -> None:
    cr = uuid.uuid4()
    with patch.object(service, "_rooms") as rooms, patch.object(service, "_sessions") as sessions:
        rooms.get = AsyncMock(return_value=_room(cr))
        sessions.find_by_browser_id = AsyncMock(return_value=None)
        with pytest.raises(GuestDisplayNameInvalid):
            await service.create_or_resume(
                chatroom_id=cr, guest_token="correct-token", display_name=_ZERO_WIDTH
            )


async def test_rename_rejects_a_name_that_normalises_to_nothing(service: GuestSessionService) -> None:
    with patch.object(service, "_sessions") as sessions:
        sessions.find_by_id = AsyncMock(return_value=_session(uuid.uuid4()))
        sessions.update_display_name = AsyncMock()
        with pytest.raises(GuestDisplayNameInvalid):
            await service.update_display_name(guest_session_id=uuid.uuid4(), display_name=_ZERO_WIDTH)
        sessions.update_display_name.assert_not_awaited()


# -- links off on create (F-20, Q-4) --


async def test_create_with_links_off_names_the_reason(service: GuestSessionService) -> None:
    cr = uuid.uuid4()
    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=_room(cr, links=False))
        with pytest.raises(GuestAccessDisabled):
            await service.create_or_resume(chatroom_id=cr, guest_token="correct-token", display_name="Alice")


async def test_a_wrong_link_on_a_links_off_room_is_still_an_invalid_link(
    service: GuestSessionService,
) -> None:
    cr = uuid.uuid4()
    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=_room(cr, links=False, token="real-token"))
        with pytest.raises(GuestTokenInvalid):
            await service.create_or_resume(chatroom_id=cr, guest_token="wrong-token", display_name="Alice")


async def test_registered_enrol_with_links_off_names_the_reason() -> None:
    """The signed-in "Enter as <account>" path holds the link too (code review finding 5)."""
    cr = uuid.uuid4()
    service = GuestService(AsyncMock())
    with patch.object(service, "_rooms") as rooms, patch.object(service, "_guests") as guests:
        rooms.get = AsyncMock(return_value=_room(cr, links=False))
        guests.add = AsyncMock()
        with pytest.raises(GuestAccessDisabled):
            await service.enroll(
                chatroom_id=cr, token="correct-token", principal=_account(), actor_ip=None, request_id=None
            )
        rooms.get = AsyncMock(return_value=_room(cr, links=False, token="real-token"))
        with pytest.raises(GuestTokenInvalid):
            await service.enroll(
                chatroom_id=cr, token="wrong-token", principal=_account(), actor_ip=None, request_id=None
            )
        guests.add.assert_not_awaited()


# -- links off on refresh: the cookie is matched first --


async def test_refresh_with_links_off_names_the_reason_and_audits_nothing(
    service: GuestSessionService,
) -> None:
    """The rotation runs first and is undone by the route's rollback on this
    refusal (spec 2026-10-05-guest-session-backend-hardening Q-6); that the cookie
    survives is proven against Postgres in test_guest_session_hardening_db."""
    cr = uuid.uuid4()
    with (
        patch(f"{_SERVICE}.ensure_room_live", AsyncMock(return_value=_room(cr, links=False))),
        patch.object(service, "_sessions") as sessions,
        patch(f"{_SERVICE}.audit") as audit,
    ):
        sessions.rotate_refresh = AsyncMock(return_value=_session(cr))
        audit.emit = AsyncMock()
        with pytest.raises(GuestAccessDisabled):
            await service.refresh(chatroom_id=cr, refresh_token="live-cookie")
        audit.emit.assert_not_awaited()


async def test_an_unmatched_cookie_on_a_links_off_room_answers_like_a_links_on_room(
    service: GuestSessionService,
) -> None:
    """Otherwise any request with an arbitrary cookie value could learn whether a
    room has its guest links off. An unknown cookie and one of another room's
    sessions both fail the rotation's predicate, before the room is read."""
    cr = uuid.uuid4()
    for links in (True, False):
        live = AsyncMock(return_value=_room(cr, links=links))
        with patch(f"{_SERVICE}.ensure_room_live", live), patch.object(service, "_sessions") as sessions:
            sessions.rotate_refresh = AsyncMock(return_value=None)
            with pytest.raises(GuestTokenInvalid):
                await service.refresh(chatroom_id=cr, refresh_token="whatever")
            live.assert_not_awaited()


# -- the guest ws ticket route checks the room --


def _guest(chatroom_id: uuid.UUID | None) -> Principal:
    return Principal(
        user_id=uuid.uuid4(), is_admin=False, email_verified=False, is_guest=True, chatroom_id=chatroom_id
    )


def _request() -> SimpleNamespace:
    return SimpleNamespace(headers={"authorization": "Bearer guest-jwt"})


@pytest.mark.parametrize("raised", [GuestAccessDisabled, ChatroomNotFound])
async def test_ticket_is_refused_when_the_room_no_longer_admits_the_guest(raised: type[Exception]) -> None:
    room = uuid.uuid4()
    facade = SimpleNamespace(
        ensure_guest_room_open=AsyncMock(side_effect=raised(str(room))), ensure_guest_session_live=AsyncMock()
    )
    mint = AsyncMock(return_value=("ticket", 30))
    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch("shared_kernel.realtime.mint_ws_ticket", mint),
        pytest.raises(raised),
    ):
        await guests_route.guest_ws_ticket(request=_request(), principal=_guest(room), db=MagicMock())
    facade.ensure_guest_room_open.assert_awaited_once_with(room)
    mint.assert_not_awaited()


async def test_ticket_is_minted_for_a_room_that_admits_guests() -> None:
    room = uuid.uuid4()
    principal = _guest(room)
    facade = SimpleNamespace(
        ensure_guest_room_open=AsyncMock(return_value=None),
        ensure_guest_session_live=AsyncMock(return_value=None),
    )
    mint = AsyncMock(return_value=("ticket", 30))
    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch("shared_kernel.realtime.mint_ws_ticket", mint),
    ):
        out = await guests_route.guest_ws_ticket(request=_request(), principal=principal, db=MagicMock())
    assert (out.ticket, out.expires_in) == ("ticket", 30)
    mint.assert_awaited_once_with("guest-jwt")
    facade.ensure_guest_session_live.assert_awaited_once_with(
        guest_session_id=principal.user_id, chatroom_id=room
    )


async def test_ticket_is_refused_for_a_removed_guest() -> None:
    """docs/tasks/2026-10-05-guest-kick-and-ban code review: otherwise every
    reconnect mints a ticket for a socket the handshake then closes 4408."""
    room = uuid.uuid4()
    facade = SimpleNamespace(
        ensure_guest_room_open=AsyncMock(return_value=None),
        ensure_guest_session_live=AsyncMock(side_effect=GuestRemoved(str(room))),
    )
    mint = AsyncMock(return_value=("ticket", 30))
    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch("shared_kernel.realtime.mint_ws_ticket", mint),
        pytest.raises(GuestRemoved),
    ):
        await guests_route.guest_ws_ticket(request=_request(), principal=_guest(room), db=MagicMock())
    mint.assert_not_awaited()


async def test_service_room_check_raises_for_links_off_and_missing_rooms(
    service: GuestSessionService,
) -> None:
    cr = uuid.uuid4()
    with patch(f"{_SERVICE}.ensure_room_live", AsyncMock(return_value=_room(cr))):
        await service.ensure_admits_guests(cr)
    with (
        patch(f"{_SERVICE}.ensure_room_live", AsyncMock(return_value=_room(cr, links=False))),
        pytest.raises(GuestAccessDisabled),
    ):
        await service.ensure_admits_guests(cr)
    with (
        patch(f"{_SERVICE}.ensure_room_live", AsyncMock(side_effect=ChatroomNotFound(str(cr)))),
        pytest.raises(ChatroomNotFound),
    ):
        await service.ensure_admits_guests(cr)


# -- the problem types the client branches on --


def test_problem_types_and_statuses() -> None:
    mapped = error_mapping._MAP
    assert mapped[GuestAccessDisabled][:2] == ("conversation/guest-access-disabled", 403)
    assert mapped[GuestDisplayNameInvalid][:2] == ("conversation/guest-display-name-invalid", 422)
