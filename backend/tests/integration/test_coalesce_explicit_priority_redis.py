"""The coalescing scripts on a real Redis ([R15.30], phase 5 AC-5).

The unit fake interprets each script by hand, so a Lua error, a wrong ARGV index
or a `false` mistaken for `nil` passes there and fails here. These run the real
`_MARK_QUEUED_LUA` and `_REPARK_QUEUED_LUA` through the engine's own helpers.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

import contexts.agents.application.runtime.turn_engine as te

pytestmark = pytest.mark.db


@pytest.fixture
async def redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Redis]:
    from app.config.settings import get_settings

    client: Redis = Redis.from_url(get_settings().redis.dsn, decode_responses=True)
    monkeypatch.setattr("shared_kernel.auth.clients.get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


async def _cleanup(redis: Redis, agent_id: uuid.UUID, room: uuid.UUID) -> None:
    await redis.delete(
        te._queued_trigger_key(agent_id, room),
        te._queued_trigger_message_key(agent_id, room),
        te._covered_key(agent_id, room),
    )


class TestExplicitPriorityOnRedis:
    async def test_an_explicit_call_displaces_an_automatic_mark(self, redis: Redis) -> None:
        agent_id, room, auto, mention = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            assert await te._mark_trigger_queued(agent_id, room, "every_n_messages", auto)
            assert await te._mark_trigger_queued(agent_id, room, "mention", mention)
            popped = await te._pop_queued_trigger(agent_id, room)
        finally:
            await _cleanup(redis, agent_id, room)

        assert (popped.trigger, popped.message_id) == ("mention", mention)

    async def test_an_automatic_mark_behind_an_explicit_call_changes_nothing(self, redis: Redis) -> None:
        agent_id, room, mention, later = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            assert await te._mark_trigger_queued(agent_id, room, "mention", mention)
            assert await te._mark_trigger_queued(agent_id, room, "silence_minutes", None)
            assert await te._mark_trigger_queued(agent_id, room, "every_n_messages", later)
            popped = await te._pop_queued_trigger(agent_id, room)
        finally:
            await _cleanup(redis, agent_id, room)

        assert (popped.trigger, popped.message_id) == ("mention", mention)

    async def test_two_automatic_marks_keep_first_wins_and_the_latest_id(self, redis: Redis) -> None:
        agent_id, room, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            assert await te._mark_trigger_queued(agent_id, room, "silence_minutes", first)
            assert await te._mark_trigger_queued(agent_id, room, "every_n_messages", second)
            popped = await te._pop_queued_trigger(agent_id, room)
        finally:
            await _cleanup(redis, agent_id, room)

        assert (popped.trigger, popped.message_id) == ("silence_minutes", second)

    async def test_an_explicit_repark_takes_the_name_and_keeps_a_newer_id(self, redis: Redis) -> None:
        agent_id, room, older, newer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            assert await te._mark_trigger_queued(agent_id, room, "every_n_messages", newer)
            await te._repark_queued_trigger(
                agent_id,
                room,
                te.QueuedTrigger(state=te.TriggerPopState.PARKED, trigger="mention", message_id=older),
            )
            popped = await te._pop_queued_trigger(agent_id, room)
        finally:
            await _cleanup(redis, agent_id, room)

        assert (popped.trigger, popped.message_id) == ("mention", newer)

    async def test_an_automatic_repark_never_displaces_a_newer_mark(self, redis: Redis) -> None:
        agent_id, room, older, newer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        try:
            assert await te._mark_trigger_queued(agent_id, room, "mention", newer)
            await te._repark_queued_trigger(
                agent_id,
                room,
                te.QueuedTrigger(
                    state=te.TriggerPopState.PARKED, trigger="every_n_messages", message_id=older
                ),
            )
            popped = await te._pop_queued_trigger(agent_id, room)
        finally:
            await _cleanup(redis, agent_id, room)

        assert (popped.trigger, popped.message_id) == ("mention", newer)
