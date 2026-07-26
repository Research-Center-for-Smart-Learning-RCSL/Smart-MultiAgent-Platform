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
    message_key = f"wakeup:msg_count:{agent_id}:{room_id}"
    autostop_key = f"wakeup:autostop:{agent_id}:{room_id}"

    assert await wakeup_state.increment_message_count(agent_id, room_id) == 7
    assert await wakeup_state.increment_autostop(agent_id, room_id) == 7
    assert redis.events == [
        ("pipeline", True),
        ("incr", message_key),
        ("expire", message_key, 604800),
        ("execute",),
        ("pipeline", True),
        ("incr", autostop_key),
        ("expire", autostop_key, 604800),
        ("execute",),
    ]


async def test_graphrag_counter_increment_and_expire_in_one_transaction(monkeypatch) -> None:
    redis = _Redis()
    monkeypatch.setattr(
        "contexts.knowledge.application.graphrag_triggers.get_redis",
        lambda: redis,
    )
    config_id = uuid.uuid4()
    key = f"graphrag:msg_count:{config_id}"

    assert await RedisGraphRagMessageCounter().increment(config_id) == 7
    assert redis.events == [
        ("pipeline", True),
        ("incr", key),
        ("expire", key, 604800),
        ("execute",),
    ]


def test_dead_message_counter_reset_is_not_exported() -> None:
    dead_name = "reset_" + "message_count"
    assert dead_name not in wakeup_state.__all__
    assert not hasattr(wakeup_state, dead_name)
