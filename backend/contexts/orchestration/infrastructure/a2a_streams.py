"""A2A Redis Streams transport (G.1).

Each agent gets a dedicated inbox stream ``a2a:agent:{agent_id}``.
Failed messages (after 3 retries) land in ``a2a:agent:{agent_id}:dlq``.

This module is pure infrastructure — no domain logic, no scope checks.
The application layer (``a2a_service.py``) wires scope + audit on top.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from shared_kernel.auth.clients import get_redis

_MAX_RETRIES: Final = 3
_BACKOFF_BASE_SECONDS: Final = 1.0
_CONSUMER_GROUP: Final = "agent-runtime"
_BLOCK_MS: Final = 1000
_STREAM_MAXLEN: Final = 10_000


def _inbox_key(agent_id: uuid.UUID) -> str:
    return f"a2a:agent:{agent_id}"


def _dlq_key(agent_id: uuid.UUID) -> str:
    return f"a2a:agent:{agent_id}:dlq"


def _consumer_name(agent_id: uuid.UUID) -> str:
    return f"consumer-{agent_id}"


async def ensure_consumer_group(agent_id: uuid.UUID) -> None:
    """Idempotently create the consumer group for an agent's inbox."""
    r = get_redis()
    key = _inbox_key(agent_id)
    try:
        await r.xgroup_create(key, _CONSUMER_GROUP, id="0", mkstream=True)  # type: ignore[arg-type]
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise


async def xadd_envelope(agent_id: uuid.UUID, envelope_json: str) -> str:
    """Append an envelope to an agent's inbox stream.

    Returns the Redis Stream entry ID.
    """
    r = get_redis()
    entry_id: Any = await r.xadd(
        _inbox_key(agent_id),
        {"envelope": envelope_json},
        maxlen=_STREAM_MAXLEN,
        approximate=True,
    )
    return str(entry_id)


async def xadd_dlq(agent_id: uuid.UUID, entry: dict[str, str]) -> str:
    """Push a failed message into the agent's DLQ stream."""
    r = get_redis()
    entry_id: Any = await r.xadd(
        _dlq_key(agent_id),
        entry,
        maxlen=_STREAM_MAXLEN,
        approximate=True,
    )
    return str(entry_id)


async def xread_pending(
    agent_id: uuid.UUID,
    count: int = 10,
) -> list[tuple[str, dict[str, str]]]:
    """Read pending (unacknowledged) entries for this consumer."""
    r = get_redis()
    consumer = _consumer_name(agent_id)
    result = await r.xreadgroup(
        _CONSUMER_GROUP,
        consumer,
        {_inbox_key(agent_id): "0"},
        count=count,
    )
    return _parse_xread(result)


async def xread_new(
    agent_id: uuid.UUID,
    count: int = 10,
    block_ms: int = _BLOCK_MS,
) -> list[tuple[str, dict[str, str]]]:
    """Block-read new entries from the agent's inbox."""
    r = get_redis()
    consumer = _consumer_name(agent_id)
    result = await r.xreadgroup(
        _CONSUMER_GROUP,
        consumer,
        {_inbox_key(agent_id): ">"},
        count=count,
        block=block_ms,
    )
    return _parse_xread(result)


async def xack(agent_id: uuid.UUID, stream_id: str) -> None:
    await get_redis().xack(_inbox_key(agent_id), _CONSUMER_GROUP, stream_id)  # type: ignore[arg-type]


def _parse_xread(
    result: Any,
) -> list[tuple[str, dict[str, str]]]:
    """Normalise xreadgroup return to a flat list of (stream_id, fields)."""
    if not result:
        return []
    entries: list[tuple[str, dict[str, str]]] = []
    for _stream_name, messages in result:
        for msg_id, fields in messages:
            entries.append((str(msg_id), {str(k): str(v) for k, v in fields.items()}))
    return entries


async def get_pending_delivery_counts(
    agent_id: uuid.UUID,
    count: int = 100,
) -> dict[str, int]:
    """Return {stream_id: delivery_count} for pending messages.

    Uses XPENDING RANGE which reports how many times each message has
    been delivered to a consumer — the authoritative retry counter.
    """
    r = get_redis()
    key = _inbox_key(agent_id)
    consumer = _consumer_name(agent_id)
    try:
        entries = await r.xpending_range(
            key, _CONSUMER_GROUP, min="-", max="+", count=count,
            consumername=consumer,
        )
    except Exception:
        return {}
    return {
        entry["message_id"]: entry["times_delivered"]
        for entry in entries
    }


async def move_to_dlq(
    agent_id: uuid.UUID,
    stream_id: str,
    envelope_json: str,
    error: str,
    attempt: int,
) -> None:
    """ACK the original and push to DLQ."""
    await xack(agent_id, stream_id)
    await xadd_dlq(agent_id, {
        "stream_id": stream_id,
        "envelope": envelope_json,
        "attempt_count": str(attempt),
        "last_error": error,
        "moved_at": datetime.now(UTC).isoformat(),
    })


async def read_dlq(
    agent_id: uuid.UUID,
    count: int = 50,
    start: str = "-",
    end: str = "+",
) -> list[dict[str, str]]:
    """Read DLQ entries for admin inspection."""
    r = get_redis()
    result = await r.xrange(_dlq_key(agent_id), min=start, max=end, count=count)
    return [
        {"stream_entry_id": str(eid), **{str(k): str(v) for k, v in fields.items()}}
        for eid, fields in (result or [])
    ]


async def wait_for_reply(
    agent_id: uuid.UUID,
    correlation_id: uuid.UUID,
    timeout_seconds: float = 60.0,
) -> dict[str, Any] | None:
    """Block until a ``reply`` with matching ``correlation_id`` arrives.

    Used by sync ``call`` (R9.15). Returns the parsed envelope dict or
    None on timeout. Non-matching messages are re-queued.
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    correlation_str = str(correlation_id)

    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            return None

        block_ms = min(int(remaining * 1000), 2000)
        entries = await xread_new(agent_id, count=1, block_ms=block_ms)
        if not entries:
            continue

        stream_id, fields = entries[0]
        raw = fields.get("envelope", "{}")
        try:
            envelope = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            await xack(agent_id, stream_id)
            continue

        if (
            envelope.get("type") == "reply"
            and envelope.get("correlation_id") == correlation_str
        ):
            await xack(agent_id, stream_id)
            return envelope

        # Not our reply — leave it pending for the normal consumer loop.
        # (Don't ACK; the consumer will pick it up.)


__all__ = [
    "ensure_consumer_group",
    "get_pending_delivery_counts",
    "move_to_dlq",
    "read_dlq",
    "wait_for_reply",
    "xack",
    "xadd_dlq",
    "xadd_envelope",
    "xread_new",
    "xread_pending",
]
