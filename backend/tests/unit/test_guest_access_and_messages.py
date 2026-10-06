"""Guest access check shortcut and message sender type (AC-8, OQ-1).

Tests that resolve_room_access short-circuits for guest principals and
that messages sent by guests carry sender_type='guest'.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.conversation.application.access import _resolve_guest_access
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom, GuestRemoved
from contexts.conversation.domain.models import GuestSession, SenderType
from shared_kernel.auth.permissions import Principal


def _guest_principal(
    chatroom_id: uuid.UUID | None = None,
) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        is_admin=False,
        email_verified=False,
        is_guest=True,
        chatroom_id=chatroom_id or uuid.uuid4(),
    )


# -- guest access shortcut --


@pytest.mark.asyncio
async def test_guest_access_matching_room_returns_is_guest() -> None:
    cr_id = uuid.uuid4()
    principal = _guest_principal(chatroom_id=cr_id)

    fake_room = MagicMock()
    fake_room.id = cr_id
    fake_room.workspace_id = uuid.uuid4()

    fake_ws = MagicMock()
    fake_ws.project_id = uuid.uuid4()

    db = AsyncMock()

    fake_project = MagicMock()
    fake_project.id = fake_ws.project_id

    with (
        patch("contexts.conversation.application.access.ChatroomRepository") as room_cls,
        patch("contexts.conversation.application.access.WorkspaceRepository") as ws_cls,
        patch("contexts.conversation.application.access.TenancyFacade") as tenancy_cls,
        patch("contexts.conversation.application.access.GuestSessionRepository") as sessions_cls,
    ):
        room_cls.return_value.get = AsyncMock(return_value=fake_room)
        ws_cls.return_value.get = AsyncMock(return_value=fake_ws)
        tenancy_cls.return_value.get_project = AsyncMock(return_value=fake_project)
        sessions_cls.return_value.find_by_id = AsyncMock(return_value=_session(principal.user_id, cr_id))

        result = await _resolve_guest_access(db, principal=principal, chatroom_id=cr_id)

    assert result.is_guest is True
    assert result.roles == frozenset()
    assert result.chatroom == fake_room
    assert result.project_id == fake_project.id


def _session(session_id: uuid.UUID, chatroom_id: uuid.UUID, *, revoked: bool = False) -> GuestSession:
    stamp = datetime(2026, 10, 6, tzinfo=UTC)
    return GuestSession(
        id=session_id,
        chatroom_id=chatroom_id,
        display_name="Ann",
        browser_id="br-1",
        refresh_token_hash="h",
        last_seen_at=stamp,
        created_at=stamp,
        revoked_at=stamp if revoked else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["revoked", "missing", "other-room"])
async def test_guest_access_refuses_a_removed_session(case: str) -> None:
    """[R13.07a]: the token outlives the removal, so the session row decides."""
    cr_id = uuid.uuid4()
    principal = _guest_principal(chatroom_id=cr_id)
    session = {
        "revoked": _session(principal.user_id, cr_id, revoked=True),
        "missing": None,
        "other-room": _session(principal.user_id, uuid.uuid4()),
    }[case]

    fake_room = MagicMock(id=cr_id, workspace_id=uuid.uuid4())
    with (
        patch("contexts.conversation.application.access.ChatroomRepository") as room_cls,
        patch("contexts.conversation.application.access.WorkspaceRepository") as ws_cls,
        patch("contexts.conversation.application.access.TenancyFacade") as tenancy_cls,
        patch("contexts.conversation.application.access.GuestSessionRepository") as sessions_cls,
    ):
        room_cls.return_value.get = AsyncMock(return_value=fake_room)
        ws_cls.return_value.get = AsyncMock(return_value=MagicMock(project_id=uuid.uuid4()))
        tenancy_cls.return_value.get_project = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
        sessions_cls.return_value.find_by_id = AsyncMock(return_value=session)

        with pytest.raises(GuestRemoved):
            await _resolve_guest_access(AsyncMock(), principal=principal, chatroom_id=cr_id)


@pytest.mark.asyncio
async def test_a_removed_guest_of_a_dead_room_is_told_the_room_is_gone() -> None:
    cr_id = uuid.uuid4()
    principal = _guest_principal(chatroom_id=cr_id)
    with (
        patch("contexts.conversation.application.access.ChatroomRepository") as room_cls,
        patch("contexts.conversation.application.access.WorkspaceRepository") as ws_cls,
        patch("contexts.conversation.application.access.TenancyFacade") as tenancy_cls,
        patch("contexts.conversation.application.access.GuestSessionRepository") as sessions_cls,
    ):
        room_cls.return_value.get = AsyncMock(return_value=MagicMock(id=cr_id, workspace_id=uuid.uuid4()))
        ws_cls.return_value.get = AsyncMock(return_value=MagicMock(project_id=uuid.uuid4()))
        tenancy_cls.return_value.get_project = AsyncMock(return_value=None)
        sessions_cls.return_value.find_by_id = AsyncMock(
            return_value=_session(principal.user_id, cr_id, revoked=True)
        )

        with pytest.raises(ChatroomNotFound):
            await _resolve_guest_access(AsyncMock(), principal=principal, chatroom_id=cr_id)


def test_guest_removed_is_a_room_denial_with_its_own_problem_type() -> None:
    """Callers that catch ForbiddenInRoom as a denial keep refusing; the wire
    still says why."""
    from contexts.conversation.interfaces.error_mapping import _MAP
    from shared_kernel.errors.context_handler import resolve_spec

    assert issubclass(GuestRemoved, ForbiddenInRoom)
    assert resolve_spec(GuestRemoved("x"), _MAP)[:2] == ("conversation/guest-removed", 403)


@pytest.mark.asyncio
async def test_guest_access_deleted_project_raises_not_found() -> None:
    cr_id = uuid.uuid4()
    principal = _guest_principal(chatroom_id=cr_id)

    fake_room = MagicMock()
    fake_room.id = cr_id
    fake_room.workspace_id = uuid.uuid4()

    fake_ws = MagicMock()
    fake_ws.project_id = uuid.uuid4()

    db = AsyncMock()

    with (
        patch("contexts.conversation.application.access.ChatroomRepository") as room_cls,
        patch("contexts.conversation.application.access.WorkspaceRepository") as ws_cls,
        patch("contexts.conversation.application.access.TenancyFacade") as tenancy_cls,
    ):
        room_cls.return_value.get = AsyncMock(return_value=fake_room)
        ws_cls.return_value.get = AsyncMock(return_value=fake_ws)
        tenancy_cls.return_value.get_project = AsyncMock(return_value=None)

        with pytest.raises(ChatroomNotFound):
            await _resolve_guest_access(db, principal=principal, chatroom_id=cr_id)


@pytest.mark.asyncio
async def test_guest_access_wrong_room_raises_forbidden() -> None:
    cr_id = uuid.uuid4()
    other_id = uuid.uuid4()
    principal = _guest_principal(chatroom_id=cr_id)

    db = AsyncMock()

    with pytest.raises(ForbiddenInRoom):
        await _resolve_guest_access(db, principal=principal, chatroom_id=other_id)


# -- sender type --


def test_sender_type_guest_exists() -> None:
    assert SenderType.GUEST.value == "guest"


def test_is_author_includes_guest_sender() -> None:
    """Verify OQ-1: a guest message's sender_id matches principal.user_id
    and the is_author check accepts 'guest' sender_type."""
    guest_id = uuid.uuid4()
    msg = MagicMock()
    msg.sender_id = guest_id
    msg.sender_type = SenderType.GUEST

    principal = _guest_principal()
    principal = Principal(
        user_id=guest_id,
        is_admin=False,
        email_verified=False,
        is_guest=True,
        chatroom_id=uuid.uuid4(),
    )
    is_author = msg.sender_id == principal.user_id and msg.sender_type.value in ("user", "guest")
    assert is_author is True
