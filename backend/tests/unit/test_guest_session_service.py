"""Guest session service (AC-1, AC-3, AC-4).

Unit tests that mock the database layer and verify the service logic:
create, resume via browser_id, cap enforcement, refresh, and token
validation.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.conversation.application.guest_session_service import (
    GuestSessionService,
    browser_id_hash,
)
from contexts.conversation.domain.errors import (
    GuestAccessDisabled,
    GuestBanNotFound,
    GuestCapReached,
    GuestRemoved,
    GuestSessionNotFound,
    GuestTokenInvalid,
)
from contexts.conversation.domain.models import GuestBan, GuestSession
from shared_kernel import audit as real_audit


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
    revoked: bool = False,
) -> GuestSession:
    return GuestSession(
        id=session_id or uuid.uuid4(),
        chatroom_id=chatroom_id or uuid.uuid4(),
        display_name=display_name,
        browser_id=browser_id,
        refresh_token_hash="somehash",
        last_seen_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        revoked_at=datetime.now(UTC) if revoked else None,
    )


_SERVICE = "contexts.conversation.application.guest_session_service"


@pytest.fixture(autouse=True)
def live_parents() -> Iterator[None]:
    """The room's workspace and project are live; test_guest_room_liveness covers the rest."""
    with patch(f"{_SERVICE}.ensure_parents_live", AsyncMock()):
        yield


@pytest.fixture
def db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def bans() -> MagicMock:
    """No bans unless a test sets one; the ban queries themselves are db-tier."""
    repo = MagicMock()
    repo.is_banned = AsyncMock(return_value=False)
    repo.create = AsyncMock(return_value=None)
    repo.delete = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def service(db: AsyncMock, bans: MagicMock) -> GuestSessionService:
    with patch(f"{_SERVICE}.GuestBanRepository", return_value=bans):
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
        patch("contexts.conversation.application.guest_session_service.audit") as mock_audit,
    ):
        rooms.get = AsyncMock(return_value=room)
        sessions.find_by_browser_id = AsyncMock(return_value=None)
        sessions.count_active = AsyncMock(return_value=0)
        sessions.create = AsyncMock(return_value=new_session)
        mock_audit.emit = AsyncMock()
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
        patch("contexts.conversation.application.guest_session_service.audit") as mock_audit,
    ):
        rooms.get = AsyncMock(return_value=room)
        sessions.find_by_browser_id = AsyncMock(return_value=existing)
        sessions.update_last_seen = AsyncMock()
        sessions.update_refresh_hash = AsyncMock()
        sessions.update_display_name = AsyncMock()
        mock_audit.emit = AsyncMock()
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

        with pytest.raises(GuestAccessDisabled):
            await service.create_or_resume(
                chatroom_id=cr_id,
                guest_token="correct-token",
                display_name="Guest",
            )


@pytest.mark.asyncio
async def test_missing_room_answers_like_a_wrong_link(service: GuestSessionService) -> None:
    """Lifecycle FU-7: room existence is not answered before the link check."""
    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=None)

        with pytest.raises(GuestTokenInvalid):
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
        patch(f"{_SERVICE}.ensure_room_live", AsyncMock(return_value=room)),
        patch.object(service, "_sessions") as sessions,
        patch("contexts.conversation.application.guest_session_service.sign_guest_token") as sign,
        patch("contexts.conversation.application.guest_session_service.token_utils") as tu,
        patch("contexts.conversation.application.guest_session_service.audit") as mock_audit,
    ):
        tu.hash_refresh.side_effect = lambda token: f"hash:{token}"
        tu.new_refresh_token.return_value = "new-refresh"
        sessions.rotate_refresh = AsyncMock(return_value=existing)
        mock_audit.emit = AsyncMock()
        sign.return_value = ("new-jwt", MagicMock())

        result = await service.refresh(
            chatroom_id=cr_id,
            refresh_token="old-refresh",
        )

    assert result.access_token == "new-jwt"
    assert result.refresh_token == "new-refresh"
    assert result.guest_session_id == existing.id
    sessions.rotate_refresh.assert_awaited_once_with(
        old_hash="hash:old-refresh", new_hash="hash:new-refresh", chatroom_id=cr_id
    )


# -- guest-room-read-and-identity AC-4: roster changes are reported --


@pytest.mark.asyncio
async def test_a_new_session_and_a_renaming_resume_change_the_roster(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    room = _fake_room(chatroom_id=cr_id)
    existing = _fake_session(chatroom_id=cr_id, browser_id="br-1", display_name="Alice")

    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
        patch("contexts.conversation.application.guest_session_service.sign_guest_token") as sign,
        patch("contexts.conversation.application.guest_session_service.audit") as mock_audit,
    ):
        rooms.get = AsyncMock(return_value=room)
        sessions.find_by_browser_id = AsyncMock(return_value=existing)
        sessions.update_last_seen = AsyncMock()
        sessions.update_refresh_hash = AsyncMock()
        sessions.update_display_name = AsyncMock()
        mock_audit.emit = AsyncMock()
        sign.return_value = ("jwt-token", MagicMock())

        same = await service.create_or_resume(
            chatroom_id=cr_id, guest_token="correct-token", display_name="Alice", browser_id="br-1"
        )
        renamed = await service.create_or_resume(
            chatroom_id=cr_id, guest_token="correct-token", display_name="Alicia", browser_id="br-1"
        )

    assert same.roster_changed is False
    assert renamed.roster_changed is True


@pytest.mark.asyncio
async def test_rename_reports_whether_the_stored_name_changed(service: GuestSessionService) -> None:
    session = _fake_session(display_name="Alice")

    with patch.object(service, "_sessions") as sessions:
        sessions.find_by_id = AsyncMock(return_value=session)
        sessions.update_display_name = AsyncMock()

        unchanged = await service.update_display_name(guest_session_id=session.id, display_name="  Alice ")
        changed = await service.update_display_name(guest_session_id=session.id, display_name="Bob")

    assert (unchanged.display_name, unchanged.changed) == ("Alice", False)
    assert (changed.display_name, changed.changed) == ("Bob", True)
    sessions.update_display_name.assert_awaited_once_with(session.id, "Bob")


# -- guest-kick-and-ban: removal, bans, rotation ([R13.07a], [R6.12]) --


def _ban(chatroom_id: uuid.UUID, session_id: uuid.UUID) -> GuestBan:
    return GuestBan(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        guest_session_id=session_id,
        display_name="Alice",
        created_by=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_a_banned_browser_is_refused_past_the_link_check(
    service: GuestSessionService, bans: MagicMock
) -> None:
    cr_id = uuid.uuid4()
    bans.is_banned = AsyncMock(return_value=True)

    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
    ):
        rooms.get = AsyncMock(return_value=_fake_room(chatroom_id=cr_id))
        sessions.find_by_browser_id = AsyncMock()
        sessions.create = AsyncMock()

        with pytest.raises(GuestRemoved):
            await service.create_or_resume(
                chatroom_id=cr_id, guest_token="correct-token", display_name="Alice", browser_id="br-1"
            )

    bans.is_banned.assert_awaited_once_with(chatroom_id=cr_id, browser_id_hash=browser_id_hash("br-1"))
    sessions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_wrong_link_is_refused_before_the_ban_is_consulted(
    service: GuestSessionService, bans: MagicMock
) -> None:
    """The ban answer is the link holder's to learn, not an outsider's (spec 禮8)."""
    bans.is_banned = AsyncMock(return_value=True)
    with patch.object(service, "_rooms") as rooms:
        rooms.get = AsyncMock(return_value=_fake_room(guest_token="real-token"))
        with pytest.raises(GuestTokenInvalid):
            await service.create_or_resume(
                chatroom_id=uuid.uuid4(), guest_token="wrong-token", display_name="A", browser_id="br-1"
            )
    bans.is_banned.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_removed_unbanned_browser_rejoins_as_a_new_session(service: GuestSessionService) -> None:
    """The repository answers no live session for the browser, so a new one is made."""
    cr_id = uuid.uuid4()
    fresh = _fake_session(chatroom_id=cr_id)
    with (
        patch.object(service, "_rooms") as rooms,
        patch.object(service, "_sessions") as sessions,
        patch(f"{_SERVICE}.sign_guest_token", return_value=("jwt", MagicMock())),
        patch(f"{_SERVICE}.audit") as mock_audit,
    ):
        rooms.get = AsyncMock(return_value=_fake_room(chatroom_id=cr_id))
        sessions.find_by_browser_id = AsyncMock(return_value=None)
        sessions.count_active = AsyncMock(return_value=0)
        sessions.create = AsyncMock(return_value=fresh)
        mock_audit.emit = AsyncMock()

        result = await service.create_or_resume(
            chatroom_id=cr_id, guest_token="correct-token", display_name="Alice", browser_id="br-1"
        )

    assert result.is_resuming is False
    assert result.guest_session_id == fresh.id


@pytest.mark.asyncio
async def test_refresh_refuses_a_revoked_session_after_the_cookie_match(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    with (
        patch(f"{_SERVICE}.ensure_room_live", AsyncMock(return_value=_fake_room(chatroom_id=cr_id))),
        patch.object(service, "_sessions") as sessions,
        patch(f"{_SERVICE}.sign_guest_token") as sign,
    ):
        sessions.rotate_refresh = AsyncMock(return_value=_fake_session(chatroom_id=cr_id, revoked=True))
        with pytest.raises(GuestRemoved):
            await service.refresh(chatroom_id=cr_id, refresh_token="cookie")
    sign.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_with_an_unmatched_cookie_never_says_removed(service: GuestSessionService) -> None:
    with patch.object(service, "_sessions") as sessions:
        sessions.rotate_refresh = AsyncMock(return_value=None)
        with pytest.raises(GuestTokenInvalid):
            await service.refresh(chatroom_id=uuid.uuid4(), refresh_token="stranger")


@pytest.mark.asyncio
async def test_remove_with_ban_revokes_bans_by_browser_hash_and_audits_both(
    service: GuestSessionService, bans: MagicMock
) -> None:
    cr_id, actor = uuid.uuid4(), uuid.uuid4()
    session = _fake_session(chatroom_id=cr_id, browser_id="br-1", display_name="Alice")
    bans.create = AsyncMock(return_value=_ban(cr_id, session.id))

    with (
        patch.object(service, "_sessions") as sessions,
        patch(f"{_SERVICE}.advisory_xact_lock", AsyncMock()) as lock,
        patch(f"{_SERVICE}.audit") as mock_audit,
    ):
        sessions.find_by_id = AsyncMock(return_value=session)
        sessions.revoke = AsyncMock(return_value=True)
        mock_audit.emit = AsyncMock()
        mock_audit.AuditEvent = real_audit.AuditEvent

        result = await service.remove(
            chatroom_id=cr_id, guest_session_id=session.id, ban=True, actor_user_id=actor
        )

    assert result.changed is True
    lock.assert_awaited_once()
    sessions.revoke.assert_awaited_once_with(session.id)
    bans.create.assert_awaited_once_with(
        chatroom_id=cr_id,
        guest_session_id=session.id,
        browser_id_hash=browser_id_hash("br-1"),
        display_name="Alice",
        created_by=actor,
    )
    events = [call.args[1] for call in mock_audit.emit.await_args_list]
    assert [e.action for e in events] == ["guest.session.removed", "guest.banned"]
    assert {e.actor_user_id for e in events} == {actor}
    assert all(e.actor_guest_room_id is None for e in events)
    assert all(e.resource_id == session.id for e in events)


@pytest.mark.asyncio
async def test_a_repeated_removal_changes_nothing_and_audits_nothing(service: GuestSessionService) -> None:
    cr_id = uuid.uuid4()
    session = _fake_session(chatroom_id=cr_id, revoked=True)
    with (
        patch.object(service, "_sessions") as sessions,
        patch(f"{_SERVICE}.advisory_xact_lock", AsyncMock()),
        patch(f"{_SERVICE}.audit") as mock_audit,
    ):
        sessions.find_by_id = AsyncMock(return_value=session)
        sessions.revoke = AsyncMock(return_value=False)
        mock_audit.emit = AsyncMock()
        mock_audit.AuditEvent = real_audit.AuditEvent

        result = await service.remove(
            chatroom_id=cr_id, guest_session_id=session.id, ban=False, actor_user_id=uuid.uuid4()
        )

    assert result.changed is False
    mock_audit.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_removing_a_session_of_another_room_is_not_found(service: GuestSessionService) -> None:
    with patch.object(service, "_sessions") as sessions:
        sessions.find_by_id = AsyncMock(return_value=_fake_session(chatroom_id=uuid.uuid4()))
        sessions.revoke = AsyncMock()
        with pytest.raises(GuestSessionNotFound):
            await service.remove(
                chatroom_id=uuid.uuid4(), guest_session_id=uuid.uuid4(), ban=True, actor_user_id=uuid.uuid4()
            )
    sessions.revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_session_without_a_browser_id_is_banned_by_session_only(
    service: GuestSessionService, bans: MagicMock
) -> None:
    cr_id = uuid.uuid4()
    session = _fake_session(chatroom_id=cr_id, browser_id=None)
    with (
        patch.object(service, "_sessions") as sessions,
        patch(f"{_SERVICE}.advisory_xact_lock", AsyncMock()),
        patch(f"{_SERVICE}.audit") as mock_audit,
    ):
        sessions.find_by_id = AsyncMock(return_value=session)
        sessions.revoke = AsyncMock(return_value=True)
        mock_audit.emit = AsyncMock()
        mock_audit.AuditEvent = real_audit.AuditEvent
        await service.remove(
            chatroom_id=cr_id, guest_session_id=session.id, ban=True, actor_user_id=uuid.uuid4()
        )
    assert bans.create.await_args.kwargs["browser_id_hash"] is None


@pytest.mark.asyncio
async def test_unban_of_an_unknown_ban_is_not_found(service: GuestSessionService) -> None:
    with pytest.raises(GuestBanNotFound):
        await service.unban(chatroom_id=uuid.uuid4(), ban_id=uuid.uuid4(), actor_user_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_unban_audits_the_lifted_ban(service: GuestSessionService, bans: MagicMock) -> None:
    cr_id, actor = uuid.uuid4(), uuid.uuid4()
    lifted = _ban(cr_id, uuid.uuid4())
    bans.delete = AsyncMock(return_value=lifted)
    with patch(f"{_SERVICE}.audit") as mock_audit:
        mock_audit.emit = AsyncMock()
        mock_audit.AuditEvent = real_audit.AuditEvent
        await service.unban(chatroom_id=cr_id, ban_id=lifted.id, actor_user_id=actor)
    bans.delete.assert_awaited_once_with(chatroom_id=cr_id, ban_id=lifted.id)
    (event,) = [call.args[1] for call in mock_audit.emit.await_args_list]
    assert (event.action, event.actor_user_id, event.resource_id) == (
        "guest.unbanned",
        actor,
        lifted.guest_session_id,
    )


@pytest.mark.asyncio
async def test_rotate_link_audits_without_the_token(service: GuestSessionService) -> None:
    cr_id, actor = uuid.uuid4(), uuid.uuid4()
    rotated = _fake_room(chatroom_id=cr_id, guest_token="new-secret-token")
    with (
        patch.object(service, "_rooms") as rooms,
        patch(f"{_SERVICE}.audit") as mock_audit,
    ):
        rooms.rotate_guest_token = AsyncMock(return_value=rotated)
        mock_audit.emit = AsyncMock()
        mock_audit.AuditEvent = real_audit.AuditEvent
        room = await service.rotate_link(chatroom_id=cr_id, actor_user_id=actor)
    assert room is rotated
    (event,) = [call.args[1] for call in mock_audit.emit.await_args_list]
    assert (event.action, event.actor_user_id, event.resource_id) == (
        "chatroom.guest_link.rotated",
        actor,
        cr_id,
    )
    assert "new-secret-token" not in repr(event.metadata)
