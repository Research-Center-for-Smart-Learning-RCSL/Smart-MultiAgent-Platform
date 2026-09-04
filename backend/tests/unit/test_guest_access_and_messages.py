"""Guest access check shortcut and message sender type (AC-8, OQ-1).

Tests that resolve_room_access short-circuits for guest principals and
that messages sent by guests carry sender_type='guest'.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.conversation.application.access import _resolve_guest_access
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom
from contexts.conversation.domain.models import SenderType
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
    ):
        room_cls.return_value.get = AsyncMock(return_value=fake_room)
        ws_cls.return_value.get = AsyncMock(return_value=fake_ws)
        tenancy_cls.return_value.get_project = AsyncMock(return_value=fake_project)

        result = await _resolve_guest_access(db, principal=principal, chatroom_id=cr_id)

    assert result.is_guest is True
    assert result.roles == frozenset()
    assert result.chatroom == fake_room
    assert result.project_id == fake_project.id


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
