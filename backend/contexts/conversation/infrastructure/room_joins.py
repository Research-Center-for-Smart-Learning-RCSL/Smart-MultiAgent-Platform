"""Recorded arrivals in a room ([R13.79]).

Presence answers "who is connected now"; this answers "who arrived recently". A
page refresh closes and reopens the socket, which presence reports as a leave
and a first connection, so an arrival only counts when the same principal has
not arrived in the same room within ``JOIN_WINDOW_SECONDS``.

  - ``ws:join_seen:{room_id}:{principal_id}`` -- the per-person window, ``SET NX EX``
  - ``ws:joins:{room_id}`` -- sorted set, member = principal id, score = epoch
    seconds of the arrival, trimmed to the newest ``MAX_RECORDED_JOINS``

Both live outside the ``ws:presence:`` prefix because ``scrub_stale_presence``
scans that prefix and tells key kinds apart by counting ``:``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from shared_kernel.auth.clients import get_redis

JOIN_WINDOW_SECONDS: Final = 30 * 60
MAX_RECORDED_JOINS: Final = 50
_JOINS_TTL_SECONDS: Final = 6 * 3600


def _seen_key(room_id: uuid.UUID, principal_id: uuid.UUID) -> str:
    return f"ws:join_seen:{room_id}:{principal_id}"


def _joins_key(room_id: uuid.UUID) -> str:
    return f"ws:joins:{room_id}"


async def record_join(room_id: uuid.UUID, principal_id: uuid.UUID, *, at: datetime) -> bool:
    """Record ``principal_id``'s arrival; ``False`` when it is inside their window."""
    r = get_redis()
    if not await r.set(_seen_key(room_id, principal_id), "1", nx=True, ex=JOIN_WINDOW_SECONDS):
        return False
    key = _joins_key(room_id)
    pipe = r.pipeline(transaction=True)
    pipe.zadd(key, {str(principal_id): at.timestamp()})
    pipe.zremrangebyrank(key, 0, -(MAX_RECORDED_JOINS + 1))
    pipe.expire(key, _JOINS_TTL_SECONDS)
    try:
        await pipe.execute()
    except Exception:
        # Without the release, the window key would keep this arrival and every
        # reconnect within the window out of the join set.
        await r.delete(_seen_key(room_id, principal_id))
        raise
    return True


async def recent_joins(room_id: uuid.UUID, *, since: datetime) -> list[tuple[uuid.UUID, datetime]]:
    """Arrivals at or after ``since``, oldest first, at most ``MAX_RECORDED_JOINS``."""
    rows = await get_redis().zrangebyscore(
        _joins_key(room_id),
        since.timestamp(),
        "+inf",
        start=0,
        num=MAX_RECORDED_JOINS,
        withscores=True,
    )
    return [(uuid.UUID(member), datetime.fromtimestamp(score, UTC)) for member, score in rows]


__all__ = ["JOIN_WINDOW_SECONDS", "MAX_RECORDED_JOINS", "recent_joins", "record_join"]
