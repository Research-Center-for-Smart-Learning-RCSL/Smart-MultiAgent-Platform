"""MessageRepository.list anchor scoping (B3).

Mirrors `test_observer_agents.py::test_observation_list_before_anchor_scoped_by_chatroom_id`:
the `before`/`since` cursor anchor lookup must not resolve a message id from a
different room the caller happens to belong to -- otherwise a member of rooms
A and B could page room A using a cursor id borrowed from room B.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from contexts.conversation.infrastructure.repositories.message_repo import MessageRepository


def _anchor_result(*, created_at: datetime, id_: uuid.UUID) -> MagicMock:
    result = MagicMock()
    result.first.return_value = SimpleNamespace(created_at=created_at, id=id_)
    return result


def _empty_page_result() -> MagicMock:
    result = MagicMock()
    result.all.return_value = []
    return result


class TestMessageListAnchorScoping:
    async def test_before_anchor_scoped_by_chatroom_id(self) -> None:
        room_id = uuid.uuid4()
        before_id = uuid.uuid4()

        db = AsyncMock()
        db.execute.side_effect = [
            _anchor_result(created_at=datetime(2026, 1, 1, tzinfo=UTC), id_=before_id),
            _empty_page_result(),
        ]

        repo = MessageRepository(db)
        await repo.list(chatroom_id=room_id, before=before_id, limit=10)

        anchor_stmt = db.execute.await_args_list[0].args[0]
        compiled = str(
            anchor_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert str(room_id) in compiled
        assert str(before_id) in compiled

    async def test_since_anchor_scoped_by_chatroom_id(self) -> None:
        room_id = uuid.uuid4()
        since_id = uuid.uuid4()

        db = AsyncMock()
        db.execute.side_effect = [
            _anchor_result(created_at=datetime(2026, 1, 1, tzinfo=UTC), id_=since_id),
            _empty_page_result(),
        ]

        repo = MessageRepository(db)
        await repo.list(chatroom_id=room_id, since=since_id, limit=10)

        anchor_stmt = db.execute.await_args_list[0].args[0]
        compiled = str(
            anchor_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert str(room_id) in compiled
        assert str(since_id) in compiled

    async def test_before_anchor_excludes_soft_deleted(self) -> None:
        """FU-2 folded into B3: the anchor lookup also excludes deleted_at rows."""
        room_id = uuid.uuid4()
        before_id = uuid.uuid4()

        db = AsyncMock()
        db.execute.side_effect = [
            _anchor_result(created_at=datetime(2026, 1, 1, tzinfo=UTC), id_=before_id),
            _empty_page_result(),
        ]

        repo = MessageRepository(db)
        await repo.list(chatroom_id=room_id, before=before_id, limit=10)

        anchor_stmt = db.execute.await_args_list[0].args[0]
        compiled = str(
            anchor_stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert "deleted_at" in compiled
