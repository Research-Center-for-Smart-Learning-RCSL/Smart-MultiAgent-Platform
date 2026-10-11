"""Keyset-paged export reads against a real database (F-6, spec §7.1).

`iter_for_export` replaces a capped, ascending `all_for_chatroom` that dropped
the newest rows past 50,000. The keyset is `(created_at, id)`, so rows that
share a timestamp (bulk inserts, imports) must neither repeat nor vanish at a
page boundary; that tie-break is SQL the unit tier cannot exercise.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.conversation.infrastructure import tables as ct
from contexts.conversation.infrastructure.repositories import MessageRepository

pytestmark = pytest.mark.db

_T0 = datetime(2026, 10, 1, tzinfo=UTC)


async def _room(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID:
    workspace_id, room_id = uuid.uuid4(), uuid.uuid4()
    await session.execute(ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="iter"))
    await session.execute(
        ct.chatrooms.insert().values(
            id=room_id, workspace_id=workspace_id, name="iter", guest_token=str(uuid.uuid4())
        )
    )
    return room_id


async def test_pages_arrive_complete_and_in_order_across_timestamp_ties(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, caller = project
    async with sessionmaker() as session:
        room_id = await _room(session, project_id)
        # 2,500 rows over three pages; every 7 consecutive rows share one
        # timestamp, so several ties straddle a page boundary.
        rows = [
            {
                "id": uuid.uuid4(),
                "chatroom_id": room_id,
                "sender_type": "user",
                "sender_id": caller,
                "content_md": f"m{i}",
                "created_at": _T0 + timedelta(seconds=i // 7),
            }
            for i in range(2500)
        ]
        await session.execute(ct.messages.insert(), rows)
        await session.commit()

        repo = MessageRepository(session)
        pages = [list(page) async for page in repo.iter_for_export(room_id, page_size=1000)]
        total = await repo.count_for_export(room_id)

    assert [len(p) for p in pages] == [1000, 1000, 500]
    got = [m.id for page in pages for m in page]
    expected = [r["id"] for r in sorted(rows, key=lambda r: (r["created_at"], r["id"]))]
    assert got == expected
    assert total == 2500


async def test_row_19_narrowing_and_window_hold_on_every_page(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> None:
    project_id, caller = project
    other = uuid.uuid4()
    async with sessionmaker() as session:
        room_id = await _room(session, project_id)
        senders = [
            ("user", caller),
            ("user", other),
            ("guest", uuid.uuid4()),
            ("agent", uuid.uuid4()),
            ("system", None),
        ]
        rows = [
            {
                "id": uuid.uuid4(),
                "chatroom_id": room_id,
                "sender_type": senders[i % 5][0],
                "sender_id": senders[i % 5][1],
                "content_md": f"m{i}",
                "created_at": _T0 + timedelta(minutes=i),
            }
            for i in range(50)
        ]
        await session.execute(ct.messages.insert(), rows)
        await session.commit()

        repo = MessageRepository(session)
        after, before = _T0 + timedelta(minutes=10), _T0 + timedelta(minutes=40)
        narrowed = [
            m
            async for page in repo.iter_for_export(
                room_id, own_user_id=caller, created_after=after, created_before=before, page_size=4
            )
            for m in page
        ]
        count = await repo.count_for_export(
            room_id, own_user_id=caller, created_after=after, created_before=before
        )

    # Row i was sent at minute i by senders[i % 5].
    expected = [
        rows[i]["id"]
        for i in range(50)
        if 10 <= i < 40 and (senders[i % 5][0] in ("agent", "system") or senders[i % 5][1] == caller)
    ]
    assert [m.id for m in narrowed] == expected
    assert count == len(expected) == 18
