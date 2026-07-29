"""Real-Postgres proof that a search page is reproducible (V-6).

Spec: ``docs/tasks/2026-07-22-search-determinism-and-highlighting/spec.md`` §8 T-3.

The unit test in ``tests/unit/test_message_repo.py`` pins the *shape* of the
ORDER BY, which is what keeps CI honest. It cannot show the shape matters,
because a mock session has no planner: the defect is that ``ORDER BY rank``
alone lets the plan choose which tied rows survive ``LIMIT``. These tests
create the tie deliberately -- 300 messages with one occurrence of the term
each, so ``ts_rank_cd`` returns the same value for all of them -- and then run
the identical query under two different plans.

The fixture also collapses ``created_at`` onto three timestamps rather than 300,
so ties survive the second sort key too and the trailing ``id`` is the only
thing left making the order total. That is the property under test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.conversation.infrastructure import tables as t
from contexts.conversation.infrastructure.repositories.message_repo import MessageRepository

# Real Postgres required (see module docstring) -- routed to the backend-db CI job.
pytestmark = pytest.mark.db

_ROWS = 300
_PAGE = 50

# Three buckets, so `rank` ties are not silently resolved by `created_at`.
_STAMPS = [
    datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    datetime(2026, 1, 3, 12, 0, tzinfo=UTC),
]


@pytest.fixture
async def tied_room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> uuid.UUID:
    """A room holding `_ROWS` messages that all rank identically.

    Cleanup rides the `project` fixture: workspaces cascade from the project and
    chatrooms/messages cascade from the workspace.
    """
    project_id, user_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()

    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="search-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="search-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            t.messages.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "chatroom_id": chatroom_id,
                    "sender_type": "user",
                    "sender_id": user_id,
                    # One occurrence of the term each; the filler differs so the
                    # rows are not literally identical, but `ts_rank_cd` takes no
                    # normalization argument, so document length does not move
                    # the rank (FU-4).
                    "content_md": f"quarterly revenue note number {i}",
                    "created_at": _STAMPS[i % len(_STAMPS)],
                }
                for i in range(_ROWS)
            ],
        )
        # Without statistics the planner estimates one matching row and picks the
        # same plan whatever the GUCs say, so the plan-toggle below would compare
        # a plan against itself.
        await session.execute(sa.text("ANALYZE messages"))
        await session.commit()

    return chatroom_id


async def _search_ids(
    sessionmaker: async_sessionmaker[AsyncSession],
    chatroom_id: uuid.UUID,
    *,
    offset: int = 0,
    seq_scan: bool = True,
) -> list[uuid.UUID]:
    """Run `search` and return the ids, optionally forcing a sequential scan.

    Disabling the bitmap scan is what forces the alternative plan: the GIN index
    on `content_tsv` is only reachable through one, so turning it off drops the
    query to a heap scan and changes the order rows arrive in.
    """
    async with sessionmaker() as session:
        if not seq_scan:
            await session.execute(sa.text("SET enable_seqscan = off"))
        else:
            await session.execute(sa.text("SET enable_bitmapscan = off"))
        repo = MessageRepository(session)
        rows = await repo.search(
            chatroom_id=chatroom_id,
            query="revenue",
            limit=_PAGE,
            offset=offset,
        )
    return [m.id for m, _rank, _snippet in rows]


async def _rewrite_heap_order(
    sessionmaker: async_sessionmaker[AsyncSession],
    chatroom_id: uuid.UUID,
) -> None:
    """Shuffle physical row order by moving one third of the rows to the heap tail.

    A no-op UPDATE still writes a new tuple version, so this reorders the heap
    without touching a single value. `VACUUM FULL` would do the same but cannot
    run inside a transaction and needs table ownership.

    Rewriting a *subset* is what makes this work: updating every row appends the
    new versions in the order they were read, which preserves relative order and
    leaves the query returning the same page even when it is broken. Toggling the
    planner GUCs alone is not enough either -- a bitmap heap scan reads in heap
    order just like a sequential scan, so both plans feed the sort the same input.
    Verified: without the `id` tiebreak this flips the returned page; with it, it
    does not.
    """
    async with sessionmaker() as session:
        await session.execute(
            sa.text(
                "UPDATE messages SET content_md = content_md"
                " WHERE chatroom_id = :room AND created_at = :stamp"
            ),
            {"room": chatroom_id, "stamp": _STAMPS[0]},
        )
        await session.commit()


async def test_identical_ranks_page_reproducibly(
    sessionmaker: async_sessionmaker[AsyncSession],
    tied_room: uuid.UUID,
) -> None:
    """The same room + query + limit returns the same page under a different plan
    and a different physical row order."""
    first = await _search_ids(sessionmaker, tied_room, seq_scan=True)

    await _rewrite_heap_order(sessionmaker, tied_room)
    second = await _search_ids(sessionmaker, tied_room, seq_scan=False)
    third = await _search_ids(sessionmaker, tied_room, seq_scan=True)

    assert len(first) == _PAGE
    assert first == second
    assert first == third


async def test_offset_pages_do_not_overlap(
    sessionmaker: async_sessionmaker[AsyncSession],
    tied_room: uuid.UUID,
) -> None:
    """Consecutive OFFSET windows partition the result set instead of overlapping."""
    page_one = await _search_ids(sessionmaker, tied_room, offset=0)
    page_two = await _search_ids(sessionmaker, tied_room, offset=_PAGE)

    assert len(page_one) == _PAGE
    assert len(page_two) == _PAGE
    assert set(page_one).isdisjoint(page_two)
    assert len(set(page_one) | set(page_two)) == 2 * _PAGE


async def test_snippet_marks_the_match(
    sessionmaker: async_sessionmaker[AsyncSession],
    tied_room: uuid.UUID,
) -> None:
    """F-22, backend end of the chain: `ts_headline` emits the `<mark>` that the
    frontend allowlist and the panel CSS are written against, not PostgreSQL's
    default `<b>`."""
    async with sessionmaker() as session:
        repo = MessageRepository(session)
        rows = await repo.search(chatroom_id=tied_room, query="revenue", limit=1)

    assert rows
    _msg, _rank, snippet = rows[0]
    assert "<mark>revenue</mark>" in snippet
    assert "<b>" not in snippet
