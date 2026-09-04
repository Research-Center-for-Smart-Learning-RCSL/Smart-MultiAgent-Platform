"""Guest session service (AC-1, AC-3, AC-4).

Unit tests that mock the database layer and verify the service logic:
create, resume via browser_id, cap enforcement, refresh, and token
validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.conversation.application.guest_session_service import (
    GuestSessionService,
)
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    GuestCapReached,
    GuestTokenInvalid,
)
from contexts.conversation.domain.models import GuestSession


def _fake_room(
    chatroom_id: uuid.UUID | None = None,
    guest_token: str = "correct-token",
    allow_guest_links: bool = True,
) -> MagicMock:
    room = MagicMock()
    room.id = chatroom_id or uuid.uuid4()
    room.guest_token = guest_token
    room.allow_guest_links = allow_guest_links
    return room


def _fake_session(
    session_id: uuid.UUID | None = None,
    chatroom_id: uuid.UUID | None = None,
    display_name: str = "Guest",
    browser_id: str | None = "br-1",
) -> GuestSession:
    return GuestSession(
        id=session_id or uuid.uuid4(),
        chatroom_id=chatroom_id or uuid.uuid4(),
        display_name=display_name,
        browser_id=browser_id,
        refresh_token_hash="somehash",
        last_seen_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(db: AsyncMock) -> GuestSessionService:
    return GuestSessionService(db)


# -- AC-1: create session --


@pytest.mark.asyncio
async def test_create_session_returns_tokens(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    room = _fake_room(chatroom_id=cr_id)
    new_session = _fake_session(chatroom_id=cr_id)

    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
        patch("contexts.conversation.application.guest_session_service.sign_guest_token") as sign,
        patch("contexts.conversation.application.guest_session_service.audit"),
    ):
        rooms.get = AsyncMock(return_value=room)
        sessions.find_by_browser_id = AsyncMock(return_value=None)
        sessions.count_active = AsyncMock(return_value=0)
        sessions.create = AsyncMock(return_value=new_session)
        sign.return_value = ("jwt-token", MagicMock())

        result = await service.create_or_resume(
            chatroom_id=cr_id,
            guest_token="correct-token",
            display_name="Alice",
        )

    assert result.access_token == "jwt-token"
    assert result.refresh_token  # non-empty
    assert result.guest_session_id == new_session.id
    assert result.is_resuming is False


# -- AC-3: resume via browser_id --


@pytest.mark.asyncio
async def test_resume_returns_is_resuming_true(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    room = _fake_room(chatroom_id=cr_id)
    existing = _fake_session(chatroom_id=cr_id, browser_id="br-1", display_name="Alice")

    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
        patch("contexts.conversation.application.guest_session_service.sign_guest_token") as sign,
        patch("contexts.conversation.application.guest_session_service.audit"),
    ):
        rooms.get = AsyncMock(return_value=room)
        sessions.find_by_browser_id = AsyncMock(return_value=existing)
        sessions.update_last_seen = AsyncMock()
        sessions.update_refresh_hash = AsyncMock()
        sessions.update_display_name = AsyncMock()
        sign.return_value = ("jwt-token", MagicMock())

        result = await service.create_or_resume(
            chatroom_id=cr_id,
            guest_token="correct-token",
            display_name="Alice",
            browser_id="br-1",
        )

    assert result.is_resuming is True
    assert result.guest_session_id == existing.id


# -- AC-4: cap enforcement --


@pytest.mark.asyncio
async def test_cap_reached_raises(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    room = _fake_room(chatroom_id=cr_id)

    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
    ):
        rooms.get = AsyncMock(return_value=room)
        sessions.find_by_browser_id = AsyncMock(return_value=None)
        sessions.count_active = AsyncMock(return_value=50)

        with pytest.raises(GuestCapReached):
            await service.create_or_resume(
                chatroom_id=cr_id,
                guest_token="correct-token",
                display_name="Overflow",
            )


# -- token validation --


@pytest.mark.asyncio
async def test_wrong_token_raises(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    room = _fake_room(chatroom_id=cr_id, guest_token="real-token")

    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=room)

        with pytest.raises(GuestTokenInvalid):
            await service.create_or_resume(
                chatroom_id=cr_id,
                guest_token="wrong-token",
                display_name="Hacker",
            )


@pytest.mark.asyncio
async def test_guest_links_disabled_raises(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    room = _fake_room(chatroom_id=cr_id, allow_guest_links=False)

    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=room)

        with pytest.raises(GuestTokenInvalid):
            await service.create_or_resume(
                chatroom_id=cr_id,
                guest_token="correct-token",
                display_name="Guest",
            )


@pytest.mark.asyncio
async def test_missing_room_raises(service: GuestSessionService) -> None:
    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=None)

        with pytest.raises(ChatroomNotFound):
            await service.create_or_resume(
                chatroom_id=uuid.uuid4(),
                guest_token="any",
                display_name="Guest",
            )


# -- refresh --


@pytest.mark.asyncio
async def test_refresh_returns_new_tokens(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    room = _fake_room(chatroom_id=cr_id)
    existing = _fake_session(chatroom_id=cr_id)

    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
        patch("contexts.conversation.application.guest_session_service.sign_guest_token") as sign,
        patch("contexts.conversation.application.guest_session_service.token_utils") as tu,
        patch("contexts.conversation.application.guest_session_service.audit"),
    ):
        rooms.get = AsyncMock(return_value=room)
        tu.hash_refresh.return_value = existing.refresh_token_hash
        tu.new_refresh_token.return_value = "new-refresh"
        sessions.find_by_refresh_hash = AsyncMock(return_value=existing)
        sessions.update_refresh_hash = AsyncMock()
        sign.return_value = ("new-jwt", MagicMock())

        result = await service.refresh(
            chatroom_id=cr_id,
            refresh_token="old-refresh",
        )

    assert result.access_token == "new-jwt"
    assert result.refresh_token == "new-refresh"
    assert result.guest_session_id == existing.id
