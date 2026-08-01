"""Real-Postgres proof that a replayed turn cannot post a second reply (AC-8).

The unit tests in ``tests/unit/test_turn_idempotency.py`` pin the wiring — the
key reaches the reply row, and the pre-check short-circuits before any provider
call. Neither can show the guarantee holds, because the guarantee is a partial
unique index and a fake repository has no constraints. These tests put two rows
carrying the same ``turn_job_id`` at a real Postgres and require the second to
be rejected.

They also pin the predicate. A unique index over ``metadata->>'turn_job_id'``
with no ``WHERE`` would treat every pre-0072 row — every user message, every
agent reply written before this shipped — as sharing a single NULL key, and the
migration would fail on the second one. See spec §8's migration risk row.

Spec: docs/tasks/2026-07-22-turn-idempotency-and-locking/spec.md (C6).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.conversation.infrastructure import tables as t
from contexts.conversation.infrastructure.repositories.message_repo import MessageRepository

# Real Postgres required: the whole point is the index (see module docstring).
pytestmark = pytest.mark.db


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> uuid.UUID:
    """A chatroom to hang messages off. No teardown of its own: cleanup rides
    the `project` fixture, since workspaces cascade from the project and
    chatrooms and messages from the workspace."""
    project_id, user_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="idem-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="idem-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.commit()
    return chatroom_id


async def _insert(session: AsyncSession, chatroom_id: uuid.UUID, metadata: dict[str, object]) -> uuid.UUID:
    message_id = uuid.uuid4()
    await session.execute(
        t.messages.insert().values(
            id=message_id,
            chatroom_id=chatroom_id,
            sender_type="agent",
            sender_id=uuid.uuid4(),
            content_md="reply",
            metadata=metadata,
        )
    )
    return message_id


async def test_a_second_reply_for_the_same_job_is_rejected(
    sessionmaker: async_sessionmaker[AsyncSession], room: uuid.UUID
) -> None:
    job_id = f"job-{uuid.uuid4()}"
    async with sessionmaker() as session:
        await _insert(session, room, {"type": "agent_reply", "turn_job_id": job_id})
        await session.commit()

    async def _replay() -> None:
        async with sessionmaker() as session:
            await _insert(session, room, {"type": "agent_reply", "turn_job_id": job_id})
            await session.commit()

    with pytest.raises(IntegrityError):
        await _replay()


async def test_different_jobs_both_land(
    sessionmaker: async_sessionmaker[AsyncSession], room: uuid.UUID
) -> None:
    async with sessionmaker() as session:
        first = await _insert(session, room, {"type": "agent_reply", "turn_job_id": f"job-{uuid.uuid4()}"})
        second = await _insert(session, room, {"type": "agent_reply", "turn_job_id": f"job-{uuid.uuid4()}"})
        await session.commit()

    async with sessionmaker() as session:
        found = (
            await session.execute(sa.select(sa.func.count()).where(t.messages.c.id.in_([first, second])))
        ).scalar_one()
    assert found == 2


async def test_rows_without_the_key_are_outside_the_index(
    sessionmaker: async_sessionmaker[AsyncSession], room: uuid.UUID
) -> None:
    """The predicate is what makes this migration index-only and backfill-free:
    many rows may carry no key at all."""
    async with sessionmaker() as session:
        ids = [await _insert(session, room, {"type": "agent_reply"}) for _ in range(3)]
        ids.append(await _insert(session, room, {}))
        await session.commit()

    async with sessionmaker() as session:
        found = (
            await session.execute(sa.select(sa.func.count()).where(t.messages.c.id.in_(ids)))
        ).scalar_one()
    assert found == 4


async def test_the_lookup_finds_the_row_the_index_protects(
    sessionmaker: async_sessionmaker[AsyncSession], room: uuid.UUID
) -> None:
    """The pre-check and the constraint must read the same expression, or a
    replay short-circuits on one and trips the other."""
    job_id = f"job-{uuid.uuid4()}"
    async with sessionmaker() as session:
        message_id = await _insert(session, room, {"type": "agent_reply", "turn_job_id": job_id})
        await session.commit()

    async with sessionmaker() as session:
        repo = MessageRepository(session)
        assert await repo.id_for_turn_job(job_id) == message_id
        assert await repo.id_for_turn_job(f"job-{uuid.uuid4()}") is None
