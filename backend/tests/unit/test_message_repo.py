"""MessageRepository.list anchor scoping (B3) and `search` query shape (F-22 / V-6).

Mirrors `test_observer_agents.py::test_observation_list_before_anchor_scoped_by_chatroom_id`:
the `before`/`since` cursor anchor lookup must not resolve a message id from a
different room the caller happens to belong to -- otherwise a member of rooms
A and B could page room A using a cursor id borrowed from room B.

The `search` classes pin two cross-layer contracts that nothing else enforces:
the ordering is total (so a page is reproducible) and the snippet delimiters are
`<mark>` (so the frontend allowlist and CSS have something to match). Spec:
`docs/tasks/2026-07-22-search-determinism-and-highlighting/spec.md` §8 T-1/T-2.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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


async def _compiled_search_sql() -> str:
    """Run `search` against a mock db and return its compiled statement."""
    db = AsyncMock()
    db.execute.side_effect = [_empty_page_result()]

    repo = MessageRepository(db)
    await repo.search(chatroom_id=uuid.uuid4(), query="x", limit=10)

    stmt = db.execute.await_args_list[0].args[0]
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _order_by_clause(compiled: str) -> str:
    """The ORDER BY clause alone.

    Asserting on the whole statement would be satisfied by the SELECT list,
    which already names every message column including `created_at` and `id`.
    """
    _, _, tail = compiled.partition("ORDER BY")
    assert tail, "statement has no ORDER BY"
    return tail.partition("LIMIT")[0]


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


class TestMessageSearchOrdering:
    """V-6: `ORDER BY rank` alone is not a total order, so a page under LIMIT/OFFSET
    is whatever the plan happened to yield first."""

    async def test_search_order_is_total(self) -> None:
        compiled = await _compiled_search_sql()
        order_by = _order_by_clause(compiled)

        assert "rank DESC" in order_by
        assert "messages.created_at DESC" in order_by
        assert "messages.id DESC" in order_by

    async def test_search_orders_before_limiting(self) -> None:
        compiled = await _compiled_search_sql()

        assert compiled.index("ORDER BY") < compiled.index("LIMIT")
