"""Route behavior for the chatroom-scoped approvals read side (F-13).

AuthZ itself (`ensure_can_read`/`resolve_room_access`) is pinned elsewhere
(`test_room_access.py`); these tests verify the route calls that gate and the
orchestration facade correctly and maps the result, per
`docs/tasks/2026-07-22-reconnect-reconciliation/spec.md` §8.

"unauthenticated -> 401" is not exercised here: it is enforced by the shared
`current_principal` FastAPI dependency, which every authenticated route uses
identically and which direct function-call tests (this project's convention
for `tests/unit/` route tests, see `test_activity_activation_routes.py`)
bypass by construction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import chatrooms
from app.api.v1.deps import PaginationParams
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.orchestration.domain.models import Approval, ApprovalMode, ApprovalState

_ROOM = uuid.uuid4()
_RUN = uuid.uuid4()
_AGENT = uuid.uuid4()
_NOW = datetime(2026, 7, 28, tzinfo=UTC)


def _approval(
    *, state: ApprovalState = ApprovalState.PENDING, chatroom_id: uuid.UUID | None = _ROOM
) -> Approval:
    return Approval(
        id=uuid.uuid4(),
        workflow_run_id=_RUN,
        mode=ApprovalMode.SINGLE,
        leader_agent_id=_AGENT,
        approver_agent_ids=(_AGENT,),
        timeout_seconds=300,
        state=state,
        started_at=_NOW,
        ended_at=None,
        chatroom_id=chatroom_id,
    )


def _principal(*, is_admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user_id=uuid.uuid4(), is_admin=is_admin)


def _allow_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chatrooms, "resolve_room_access", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(chatrooms, "ensure_can_read", MagicMock())


async def test_room_reader_lists_pending_and_resolved_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_read(monkeypatch)
    pending = _approval(state=ApprovalState.PENDING)
    resolved = _approval(state=ApprovalState.APPROVED)
    facade = MagicMock()
    facade.list_approvals_for_chatroom_with_votes = AsyncMock(return_value=[(pending, []), (resolved, [])])
    monkeypatch.setattr(chatrooms, "OrchestrationFacade", lambda _db: facade)

    result = await chatrooms.list_chatroom_approvals(
        chatroom_id=_ROOM,
        pagination=PaginationParams(limit=100, offset=0),
        principal=_principal(),
        db=MagicMock(),
    )

    assert {r.id for r in result} == {str(pending.id), str(resolved.id)}
    assert {r.state for r in result} == {ApprovalState.PENDING, ApprovalState.APPROVED}
    facade.list_approvals_for_chatroom_with_votes.assert_awaited_once_with(_ROOM)


async def test_approval_for_a_different_room_is_not_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    # Scoping is the facade/repository's WHERE clause, not a client-side
    # filter here -- this pins that the route asks for THIS room specifically.
    _allow_read(monkeypatch)
    facade = MagicMock()
    facade.list_approvals_for_chatroom_with_votes = AsyncMock(return_value=[])
    monkeypatch.setattr(chatrooms, "OrchestrationFacade", lambda _db: facade)

    result = await chatrooms.list_chatroom_approvals(
        chatroom_id=_ROOM,
        pagination=PaginationParams(limit=100, offset=0),
        principal=_principal(),
        db=MagicMock(),
    )

    assert result == []
    facade.list_approvals_for_chatroom_with_votes.assert_awaited_once_with(_ROOM)


async def test_non_member_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chatrooms, "resolve_room_access", AsyncMock(return_value=MagicMock()))

    def _deny(*_args: object, **_kwargs: object) -> None:
        raise ForbiddenInRoom("caller cannot read this chatroom")

    monkeypatch.setattr(chatrooms, "ensure_can_read", _deny)
    monkeypatch.setattr(chatrooms, "OrchestrationFacade", lambda _db: MagicMock())

    with pytest.raises(ForbiddenInRoom):
        await chatrooms.list_chatroom_approvals(
            chatroom_id=_ROOM,
            pagination=PaginationParams(limit=100, offset=0),
            principal=_principal(),
            db=MagicMock(),
        )


async def test_pre_migration_rows_are_absent_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # chatroom_id IS NULL rows are excluded by the repository's WHERE clause
    # (Q-4: deliberately no backfill) -- the route sees an empty list, not an
    # error, for a room whose only approvals predate the column.
    _allow_read(monkeypatch)
    facade = MagicMock()
    facade.list_approvals_for_chatroom_with_votes = AsyncMock(return_value=[])
    monkeypatch.setattr(chatrooms, "OrchestrationFacade", lambda _db: facade)

    result = await chatrooms.list_chatroom_approvals(
        chatroom_id=_ROOM,
        pagination=PaginationParams(limit=100, offset=0),
        principal=_principal(),
        db=MagicMock(),
    )

    assert result == []


async def test_results_are_paginated(monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_read(monkeypatch)
    approvals = [_approval() for _ in range(5)]
    facade = MagicMock()
    facade.list_approvals_for_chatroom_with_votes = AsyncMock(return_value=[(a, []) for a in approvals])
    monkeypatch.setattr(chatrooms, "OrchestrationFacade", lambda _db: facade)

    result = await chatrooms.list_chatroom_approvals(
        chatroom_id=_ROOM,
        pagination=PaginationParams(limit=2, offset=1),
        principal=_principal(),
        db=MagicMock(),
    )

    assert [r.id for r in result] == [str(a.id) for a in approvals[1:3]]
