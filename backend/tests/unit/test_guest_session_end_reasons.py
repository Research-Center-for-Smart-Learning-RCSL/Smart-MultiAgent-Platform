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
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.v1.guests as guests_route
from contexts.conversation.application.guest_session_service import GuestSessionService
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    GuestAccessDisabled,
    GuestDisplayNameInvalid,
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


# -- links off on refresh: the cookie is matched first --


async def test_refresh_with_links_off_names_the_reason_and_rotates_nothing(
    service: GuestSessionService,
) -> None:
    cr = uuid.uuid4()
    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
        patch(f"{_SERVICE}.audit") as audit,
    ):
        rooms.get = AsyncMock(return_value=_room(cr, links=False))
        sessions.find_by_refresh_hash = AsyncMock(return_value=_session(cr))
        sessions.update_refresh_hash = AsyncMock()
        audit.emit = AsyncMock()
        with pytest.raises(GuestAccessDisabled):
            await service.refresh(chatroom_id=cr, refresh_token="live-cookie")
        sessions.update_refresh_hash.assert_not_awaited()


@pytest.mark.parametrize("cookie", ["unknown", "foreign"])
async def test_an_unmatched_cookie_on_a_links_off_room_answers_like_a_links_on_room(
    service: GuestSessionService, cookie: str
) -> None:
    """Otherwise any request with an arbitrary cookie value could learn whether a
    room has its guest links off."""
    cr = uuid.uuid4()
    found = None if cookie == "unknown" else _session(uuid.uuid4())
    for links in (True, False):
        with patch.object(service, "_rooms") as rooms, patch.object(service, "_sessions") as sessions:
            rooms.get = AsyncMock(return_value=_room(cr, links=links))
            sessions.find_by_refresh_hash = AsyncMock(return_value=found)
            sessions.update_refresh_hash = AsyncMock()
            with pytest.raises(GuestTokenInvalid):
                await service.refresh(chatroom_id=cr, refresh_token="whatever")
            sessions.update_refresh_hash.assert_not_awaited()


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
    facade = SimpleNamespace(ensure_guest_room_open=AsyncMock(side_effect=raised(str(room))))
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
    facade = SimpleNamespace(ensure_guest_room_open=AsyncMock(return_value=None))
    mint = AsyncMock(return_value=("ticket", 30))
    with (
        patch.object(guests_route, "ConversationFacade", return_value=facade),
        patch("shared_kernel.realtime.mint_ws_ticket", mint),
    ):
        out = await guests_route.guest_ws_ticket(request=_request(), principal=_guest(room), db=MagicMock())
    assert (out.ticket, out.expires_in) == ("ticket", 30)
    mint.assert_awaited_once_with("guest-jwt")


async def test_service_room_check_raises_for_links_off_and_missing_rooms(
    service: GuestSessionService,
) -> None:
    cr = uuid.uuid4()
    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=_room(cr))
        await service.ensure_admits_guests(cr)
        rooms.get = AsyncMock(return_value=_room(cr, links=False))
        with pytest.raises(GuestAccessDisabled):
            await service.ensure_admits_guests(cr)
        rooms.get = AsyncMock(return_value=None)
        with pytest.raises(ChatroomNotFound):
            await service.ensure_admits_guests(cr)


# -- the problem types the client branches on --


def test_problem_types_and_statuses() -> None:
    mapped = error_mapping._MAP
    assert mapped[GuestAccessDisabled][:2] == ("conversation/guest-access-disabled", 403)
    assert mapped[GuestDisplayNameInvalid][:2] == ("conversation/guest-display-name-invalid", 422)
