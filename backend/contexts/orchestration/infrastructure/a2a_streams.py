"""A2A Redis Streams transport (G.1).

Each agent gets a dedicated inbox stream ``a2a:agent:{agent_id}``.
Failed messages (after 3 retries) land in ``a2a:agent:{agent_id}:dlq``.

This module is pure infrastructure — no domain logic, no scope checks.
The application layer (``a2a_service.py``) wires scope + audit on top.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from shared_kernel.auth.clients import get_redis

_MAX_RETRIES: Final = 3
_BACKOFF_BASE_SECONDS: Final = 1.0
_CONSUMER_GROUP: Final = "agent-runtime"
_BLOCK_MS: Final = 1000
_STREAM_MAXLEN: Final = 10_000
# Unique per worker process — a bare PID is not enough since replicas in
# separate containers reuse PIDs. Suffixed onto every consumer name so two A2A
# consumer processes never share a consumer identity within a group.
_PROCESS_ID: Final = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
# Inbox entries delivered to a consumer but left un-ACKed longer than this are
# treated as stranded by a crashed/stalled process and reclaimed by a live one.
_CLAIM_MIN_IDLE_MS: Final = 60_000


def _inbox_key(agent_id: uuid.UUID) -> str:
    return f"a2a:agent:{agent_id}"


def _dlq_key(agent_id: uuid.UUID) -> str:
    return f"a2a:agent:{agent_id}:dlq"


def _consumer_name(agent_id: uuid.UUID) -> str:
    """Per-(agent, process) consumer identity within the inbox group.

    The ``_PROCESS_ID`` suffix is essential: two worker replicas sharing a
    consumer name would both re-read the *same* group PEL with ``XREADGROUP 0``
    and double-process every pending entry. With distinct names each replica
    owns only its own in-flight entries; a crashed replica's entries are
    reclaimed by :func:`xautoclaim_stale`.
    """
    return f"consumer-{agent_id}-{_PROCESS_ID}"


async def ensure_consumer_group(agent_id: uuid.UUID) -> None:
    """Idempotently create the consumer group for an agent's inbox."""
    r = get_redis()
    key = _inbox_key(agent_id)
    try:
        await r.xgroup_create(key, _CONSUMER_GROUP, id="0", mkstream=True)
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
    await get_redis().xack(_inbox_key(agent_id), _CONSUMER_GROUP, stream_id)


async def xautoclaim_stale(
    agent_id: uuid.UUID,
    count: int = 10,
) -> int:
    """Reclaim inbox entries stranded by a crashed or stalled consumer.

    Per-process consumer names (see :func:`_consumer_name`) mean a worker that
    dies mid-processing leaves delivered-but-un-ACKed entries in the group PEL
    under a consumer name no live process will ever re-read. ``XAUTOCLAIM``
    transfers ownership of entries idle longer than ``_CLAIM_MIN_IDLE_MS`` to
    *this* process; they then surface in this consumer's normal pending drain
    (:func:`xread_pending`) and are retried instead of stranded forever. This
    also covers the single-worker restart case — a fresh process reclaims its
    predecessor's in-flight entries by idle time rather than by name reuse.

    Returns the number of entries reclaimed.
    """
    r = get_redis()
    result: Any = await r.xautoclaim(
        _inbox_key(agent_id),
        _CONSUMER_GROUP,
        _consumer_name(agent_id),
        _CLAIM_MIN_IDLE_MS,
        "0-0",
        count=count,
    )
    # redis-py returns [next_cursor, [(id, fields), ...]] on Redis 6.2 and
    # [next_cursor, [...], [deleted_ids]] on Redis 7+. The reclaimed entries are
    # now in this consumer's PEL; we only need their count for logging.
    if not result or len(result) < 2:
        return 0
    return len(result[1] or [])


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


async def move_to_dlq(
    agent_id: uuid.UUID,
    stream_id: str,
    envelope_json: str,
    error: str,
    attempt: int,
) -> None:
    """ACK the original and push to DLQ."""
    await xack(agent_id, stream_id)
    await xadd_dlq(
        agent_id,
        {
            "stream_id": stream_id,
            "envelope": envelope_json,
            "attempt_count": str(attempt),
            "last_error": error,
            "moved_at": datetime.now(UTC).isoformat(),
        },
    )


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


__all__ = [
    "ensure_consumer_group",
    "move_to_dlq",
    "read_dlq",
    "xack",
    "xadd_dlq",
    "xadd_envelope",
    "xautoclaim_stale",
    "xread_new",
    "xread_pending",
]
