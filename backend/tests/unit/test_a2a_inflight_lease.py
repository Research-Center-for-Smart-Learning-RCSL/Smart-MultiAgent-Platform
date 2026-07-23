"""F-5: an in-flight lease must make one inbox envelope run its handler once.

The processed marker is written only *after* the handler returns, so it cannot
stop a peer that reclaimed the entry (XAUTOCLAIM) from running the same handler
concurrently. ``_process_entry`` holds a lease for the whole attempt instead.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import contexts.orchestration.application.a2a_consumer as consumer
from contexts.orchestration.domain.models import A2AEnvelope, A2AMessageType


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.kv.pop(key, None)
        self.hashes.pop(key, None)

    async def hgetall(self, key: str) -> dict:
        return dict(self.hashes.get(key, {}))

    async def hset(self, key: str, mapping: dict) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    async def expire(self, key: str, ttl: int) -> None:
        return None


def _entry_fields() -> dict[str, str]:
    env = A2AEnvelope(
        id=uuid.uuid4(),
        from_agent=None,
        to_agent=str(uuid.uuid4()),
        workflow_run_id=None,
        type=A2AMessageType.CALL,
        payload={"input": "hi"},
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )
    return {"envelope": json.dumps(env.to_dict())}


@pytest.fixture
def _patched(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(consumer, "get_redis", lambda: fake)
    xack = AsyncMock()
    move_to_dlq = AsyncMock()
    xclaim_refresh = AsyncMock()
    monkeypatch.setattr(consumer.a2a_streams, "xack", xack)
    monkeypatch.setattr(consumer.a2a_streams, "move_to_dlq", move_to_dlq)
    monkeypatch.setattr(consumer.a2a_streams, "xclaim_refresh", xclaim_refresh)
    return fake, xack, move_to_dlq


@pytest.mark.asyncio
async def test_concurrent_process_entry_runs_handler_once(_patched) -> None:
    fake, _xack, _dlq = _patched
    agent_id = uuid.uuid4()
    stream_id = "1-0"
    fields = _entry_fields()

    release = asyncio.Event()
    calls = 0

    async def handler(_env) -> None:
        nonlocal calls
        calls += 1
        await release.wait()

    task_a = asyncio.create_task(consumer._process_entry(agent_id, stream_id, fields, handler, 1))
    task_b = asyncio.create_task(consumer._process_entry(agent_id, stream_id, fields, handler, 1))
    # Let both reach their lease-acquire / handler-await point.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    release.set()
    rc_a, rc_b = await asyncio.gather(task_a, task_b)

    assert calls == 1
    assert sorted([rc_a, rc_b]) == [0, 1]  # loser returns 0, winner 1


@pytest.mark.asyncio
async def test_inflight_loser_does_not_ack_or_dlq(_patched) -> None:
    fake, xack, move_to_dlq = _patched
    agent_id = uuid.uuid4()
    stream_id = "1-0"
    fields = _entry_fields()

    release = asyncio.Event()

    async def handler(_env) -> None:
        await release.wait()

    winner = asyncio.create_task(consumer._process_entry(agent_id, stream_id, fields, handler, 1))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Second delivery while the winner still holds the lease and blocks in-handler.
    rc_loser = await consumer._process_entry(agent_id, stream_id, fields, handler, 1)

    assert rc_loser == 0
    # The loser settled nothing: no ACK, no DLQ, no retry record.
    assert xack.await_count == 0
    assert move_to_dlq.await_count == 0
    assert not any(k.startswith("a2a:retry:") for k in fake.hashes)

    release.set()
    assert await winner == 1


@pytest.mark.asyncio
async def test_lease_released_on_handler_failure(_patched) -> None:
    fake, _xack, _dlq = _patched
    agent_id = uuid.uuid4()
    stream_id = "2-0"
    fields = _entry_fields()

    async def handler(_env) -> None:
        raise RuntimeError("boom")

    rc = await consumer._process_entry(agent_id, stream_id, fields, handler, 1)

    assert rc == 0
    # Lease gone so the backoff retry can re-acquire it next round.
    assert await fake.exists(consumer._inflight_key(agent_id, stream_id)) == 0


@pytest.mark.asyncio
async def test_lease_released_on_dlq(_patched) -> None:
    fake, _xack, move_to_dlq = _patched
    agent_id = uuid.uuid4()
    stream_id = "3-0"
    fields = _entry_fields()

    async def handler(_env) -> None:
        raise RuntimeError("boom")

    # Final attempt -> DLQ branch.
    rc = await consumer._process_entry(agent_id, stream_id, fields, handler, consumer._MAX_RETRIES)

    assert rc == 0
    assert move_to_dlq.await_count == 1
    assert await fake.exists(consumer._inflight_key(agent_id, stream_id)) == 0
    assert await fake.exists(consumer._retry_key(agent_id, stream_id)) == 0
