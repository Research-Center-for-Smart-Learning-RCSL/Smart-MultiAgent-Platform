from __future__ import annotations

import uuid

from contexts.knowledge.application.graphrag_triggers import RedisGraphRagMessageCounter
from contexts.orchestration.infrastructure import wakeup_state


class _Pipeline:
    def __init__(self, events):
        self._events = events

    def incr(self, key):
        self._events.append(("incr", key))
        return self

    def expire(self, key, ttl):
        self._events.append(("expire", key, ttl))
        return self

    async def execute(self):
        self._events.append(("execute",))
        return [7, True]


class _Redis:
    def __init__(self):
        self.events = []

    def pipeline(self, *, transaction):
        self.events.append(("pipeline", transaction))
        return _Pipeline(self.events)

    async def incr(self, key):
        self.events.append(("bare_incr", key))
        return 7

    async def expire(self, key, ttl):
        self.events.append(("bare_expire", key, ttl))
        return True


async def test_wakeup_counters_increment_and_expire_in_one_transaction(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(wakeup_state, "get_redis", lambda: redis)
    agent_id, room_id = uuid.uuid4(), uuid.uuid4()

    assert await wakeup_state.increment_message_count(agent_id, room_id) == 7
    assert await wakeup_state.increment_autostop(agent_id, room_id) == 7
    assert sum(event == ("execute",) for event in redis.events) == 2
    assert not any(event[0].startswith("bare_") for event in redis.events)


async def test_graphrag_counter_increment_and_expire_in_one_transaction(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(
        "contexts.knowledge.application.graphrag_triggers.get_redis",
        lambda: redis,
    )

    assert await RedisGraphRagMessageCounter().increment(uuid.uuid4()) == 7
    assert sum(event == ("execute",) for event in redis.events) == 1
    assert not any(event[0].startswith("bare_") for event in redis.events)


def test_dead_message_counter_reset_is_not_exported() -> None:
    assert "reset_message_count" not in wakeup_state.__all__
    assert not hasattr(wakeup_state, "reset_message_count")
