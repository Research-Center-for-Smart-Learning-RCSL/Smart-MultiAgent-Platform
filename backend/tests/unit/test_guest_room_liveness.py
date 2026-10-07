"""A room whose workspace or project was deleted is an invalid link -- AC-3 (F-16).

Spec: ``docs/tasks/2026-10-05-guest-session-backend-hardening/spec.md`` §7.3, Q-3.

Workspace and project soft deletes do not cascade to rooms, so "the chatroom row
is not deleted" is not "the room is live". The repositories are patched at the
class, not on the service instance, so the tests describe what the database
holds rather than which repository the service happens to ask.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.v1.guests as guests_route
from contexts.conversation.application.guest_session_service import GuestSessionService
from contexts.conversation.domain.errors import ChatroomNotFound, GuestTokenInvalid
from contexts.conversation.domain.models import GuestSession
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    GuestSessionRepository,
    WorkspaceRepository,
)
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.auth.permissions import Principal

_SERVICE = "contexts.conversation.application.guest_session_service"
_TOKEN = "correct-token"


def _room(chatroom_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=chatroom_id, workspace_id=uuid.uuid4(), guest_token=_TOKEN, allow_guest_links=True
    )


def _session(chatroom_id: uuid.UUID) -> GuestSession:
    return GuestSession(
        id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        display_name="Ann",
        browser_id=None,
        refresh_token_hash="hash",
        last_seen_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


@pytest.fixture(params=["workspace", "project"])
def dead_room(request: pytest.FixtureRequest) -> Iterator[uuid.UUID]:
    """A live chatroom row whose workspace or project is soft-deleted."""
    cid = uuid.uuid4()
    session = _session(cid)
    workspace = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())
    with ExitStack() as stack:
        stack.enter_context(patch.object(ChatroomRepository, "get", AsyncMock(return_value=_room(cid))))
        stack.enter_context(
            patch.object(
                WorkspaceRepository,
                "get",
                AsyncMock(return_value=None if request.param == "workspace" else workspace),
            )
        )
        stack.enter_context(
            patch.object(
                TenancyFacade,
                "get_project",
                AsyncMock(
                    return_value=None if request.param == "project" else SimpleNamespace(id=uuid.uuid4())
                ),
            )
        )
        for name, value in (
            ("find_by_browser_id", None),
            ("count_active", 0),
            ("create", session),
            ("rotate_refresh", session),
        ):
            stack.enter_context(patch.object(GuestSessionRepository, name, AsyncMock(return_value=value)))
        stack.enter_context(patch(f"{_SERVICE}.sign_guest_token", return_value=("jwt", MagicMock())))
        stack.enter_context(patch(f"{_SERVICE}.audit.emit", AsyncMock()))
        yield cid


async def test_joining_a_dead_room_is_an_invalid_link(dead_room: uuid.UUID) -> None:
    with pytest.raises(ChatroomNotFound):
        await GuestSessionService(AsyncMock()).create_or_resume(
            chatroom_id=dead_room, guest_token=_TOKEN, display_name="Ann"
        )


async def test_refreshing_in_a_dead_room_is_an_invalid_link(dead_room: uuid.UUID) -> None:
    with pytest.raises(ChatroomNotFound):
        await GuestSessionService(AsyncMock()).refresh(chatroom_id=dead_room, refresh_token="cookie")


async def test_a_ticket_for_a_dead_room_is_refused(dead_room: uuid.UUID) -> None:
    guest = Principal(
        user_id=uuid.uuid4(), is_admin=False, email_verified=False, is_guest=True, chatroom_id=dead_room
    )
    mint = AsyncMock(return_value=("ticket", 30))
    with patch("shared_kernel.realtime.mint_ws_ticket", mint), pytest.raises(ChatroomNotFound):
        await guests_route.guest_ws_ticket(
            request=SimpleNamespace(headers={"authorization": "Bearer guest-jwt"}),  # type: ignore[arg-type]
            principal=guest,
            db=AsyncMock(),
        )
    mint.assert_not_awaited()


# -- lifecycle FU-7: room existence is not answered before the link or cookie --


async def test_joining_a_missing_room_answers_like_a_wrong_link() -> None:
    with (
        patch.object(ChatroomRepository, "get", AsyncMock(return_value=None)),
        pytest.raises(GuestTokenInvalid),
    ):
        await GuestSessionService(AsyncMock()).create_or_resume(
            chatroom_id=uuid.uuid4(), guest_token="any-token", display_name="Ann"
        )


async def test_a_non_ascii_link_on_an_existing_room_answers_like_a_wrong_link() -> None:
    """Security audit: compare_digest on non-ASCII str raises, which answered 500
    for an existing room and 404 for a missing one."""
    with (
        patch.object(ChatroomRepository, "get", AsyncMock(return_value=_room(uuid.uuid4()))),
        pytest.raises(GuestTokenInvalid),
    ):
        await GuestSessionService(AsyncMock()).create_or_resume(
            chatroom_id=uuid.uuid4(), guest_token="aaaaaaaaaaaaaaaé", display_name="Ann"
        )


async def test_refreshing_in_a_missing_room_with_an_unknown_cookie_answers_like_a_bad_cookie() -> None:
    with (
        patch.object(ChatroomRepository, "get", AsyncMock(return_value=None)),
        patch.object(GuestSessionRepository, "rotate_refresh", AsyncMock(return_value=None)),
        pytest.raises(GuestTokenInvalid),
    ):
        await GuestSessionService(AsyncMock()).refresh(chatroom_id=uuid.uuid4(), refresh_token="unknown")
