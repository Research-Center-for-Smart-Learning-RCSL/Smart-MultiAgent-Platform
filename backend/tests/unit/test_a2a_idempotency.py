"""A2A consumer idempotency — a re-delivered entry must not re-run the turn."""

from __future__ import annotations

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

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.kv[key] = value

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
        type=A2AMessageType.NOTIFY,
        payload={"input": "hi"},
        correlation_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
    )
    return {"envelope": json.dumps(env.to_dict())}


@pytest.mark.asyncio
async def test_redelivery_skips_handler(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(consumer, "get_redis", lambda: fake)
    xack = AsyncMock()
    monkeypatch.setattr(consumer.a2a_streams, "xack", xack)

    handler = AsyncMock()
    agent_id = uuid.uuid4()
    stream_id = "1-0"
    fields = _entry_fields()

    # First delivery runs the handler and acks.
    rc = await consumer._process_entry(agent_id, stream_id, fields, handler, 1)
    assert rc == 1
    assert handler.await_count == 1
    assert xack.await_count == 1

    # Re-delivery of the SAME stream entry must not re-run the handler.
    rc2 = await consumer._process_entry(agent_id, stream_id, fields, handler, 1)
    assert rc2 == 1
    assert handler.await_count == 1  # unchanged
    assert xack.await_count == 2  # acked again


@pytest.mark.asyncio
async def test_failed_handler_leaves_no_marker(monkeypatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr(consumer, "get_redis", lambda: fake)
    monkeypatch.setattr(consumer.a2a_streams, "xack", AsyncMock())

    handler = AsyncMock(side_effect=RuntimeError("boom"))
    agent_id = uuid.uuid4()
    stream_id = "2-0"
    fields = _entry_fields()

    rc = await consumer._process_entry(agent_id, stream_id, fields, handler, 1)
    assert rc == 0
    # No processed marker — a retry must be free to run the handler again.
    assert await fake.exists(consumer._processed_key(agent_id, stream_id)) == 0
