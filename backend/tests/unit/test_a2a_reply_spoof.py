"""A2A reply rendezvous must drop replies from an agent other than the callee."""

from __future__ import annotations

import json
import uuid
from collections.abc import Set

import pytest

import contexts.orchestration.infrastructure.a2a_rendezvous as rv


class _Pipe:
    def __init__(self, store: dict[str, list[str]]) -> None:
        self._store = store
        self._ops: list[tuple] = []

    def rpush(self, key: str, value: str) -> None:
        self._ops.append(("rpush", key, value))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key))

    async def execute(self) -> None:
        for op in self._ops:
            if op[0] == "rpush":
                self._store.setdefault(op[1], []).append(op[2])


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def sadd(self, key: str, value: str) -> None:
        self.sets.setdefault(key, set()).add(value)

    async def srem(self, key: str, value: str) -> None:
        self.sets.get(key, set()).discard(value)

    async def smembers(self, key: str) -> Set[str]:
        return self.sets.get(key, set())

    async def expire(self, key: str, ttl: int) -> None:
        return None

    def pipeline(self, transaction: bool = False) -> _Pipe:
        return _Pipe(self.lists)


@pytest.fixture
def fake(monkeypatch) -> _FakeRedis:
    f = _FakeRedis()
    monkeypatch.setattr(rv, "get_redis", lambda: f)
    return f


@pytest.mark.asyncio
async def test_reply_from_wrong_agent_dropped(fake: _FakeRedis) -> None:
    cid = uuid.uuid4()
    callee = uuid.uuid4()
    impostor = uuid.uuid4()
    await rv.register_expected_responder(cid, callee)

    await rv.deliver_reply(cid, {"from_agent": str(impostor), "payload": {"reply": "x"}})
    assert fake.lists.get(rv._reply_key(cid)) is None  # dropped


@pytest.mark.asyncio
async def test_reply_from_callee_delivered(fake: _FakeRedis) -> None:
    cid = uuid.uuid4()
    callee = uuid.uuid4()
    await rv.register_expected_responder(cid, callee)

    await rv.deliver_reply(cid, {"from_agent": str(callee), "payload": {"reply": "ok"}})
    stored = fake.lists.get(rv._reply_key(cid))
    assert stored is not None
    assert json.loads(stored[0])["payload"]["reply"] == "ok"


@pytest.mark.asyncio
async def test_reply_with_missing_from_agent_dropped(fake: _FakeRedis) -> None:
    # A forged reply that omits from_agent must NOT bypass the responder binding.
    cid = uuid.uuid4()
    await rv.register_expected_responder(cid, uuid.uuid4())
    await rv.deliver_reply(cid, {"from_agent": None, "payload": {"reply": "x"}})
    assert fake.lists.get(rv._reply_key(cid)) is None  # dropped


@pytest.mark.asyncio
async def test_reply_without_registration_delivered(fake: _FakeRedis) -> None:
    # No expected responder bound (e.g. legacy/in-flight) -> deliver as before.
    cid = uuid.uuid4()
    await rv.deliver_reply(cid, {"from_agent": str(uuid.uuid4()), "payload": {}})
    assert fake.lists.get(rv._reply_key(cid)) is not None


@pytest.mark.asyncio
async def test_cancelling_a_workflow_run_signals_each_live_call(fake: _FakeRedis) -> None:
    run_id = uuid.uuid4()
    first = uuid.uuid4()
    second = uuid.uuid4()
    await rv.register_workflow_call(run_id, first)
    await rv.register_workflow_call(run_id, second)

    await rv.cancel_workflow_calls(run_id)

    assert await rv.is_call_cancelled(first)
    assert await rv.is_call_cancelled(second)
    assert fake.kv[rv._workflow_cancel_key(run_id)] == "1"


@pytest.mark.asyncio
async def test_call_registered_after_run_cancellation_is_cancelled_immediately(fake: _FakeRedis) -> None:
    run_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    await rv.cancel_workflow_calls(run_id)

    already_cancelled = await rv.register_workflow_call(run_id, correlation_id)

    assert already_cancelled is True
    assert await rv.is_call_cancelled(correlation_id)
