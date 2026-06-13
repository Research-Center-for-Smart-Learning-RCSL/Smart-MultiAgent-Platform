"""Per-(agent, chatroom) turn lock (K.2).

One concurrent agent turn per room: the turn engine acquires this before
running and releases it after. SET-NX-EX gives atomic acquire + TTL (so a
crashed worker self-heals); release is a token compare-and-delete (Lua) so a
turn can never delete a lock a *later* turn re-acquired after its TTL lapsed.

There is no generic distributed-lock helper in ``shared_kernel`` — this mirrors
the SET-NX-EX idiom from ``contexts/knowledge/infrastructure/redis_lock.py``,
specialised for the agent-turn key and made safe-release.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from shared_kernel.auth.clients import get_redis

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

_log = logging.getLogger(__name__)

# Default TTL comfortably exceeds a normal turn; a turn that outlives it has
# almost certainly died, and the lock should free for the next trigger.
DEFAULT_TURN_TTL_S = 300

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then " "return redis.call('del', KEYS[1]) else return 0 end"
)

# Token compare-then-PEXPIRE: the heartbeat must never refresh a lock a later
# turn re-acquired after this turn's TTL lapsed (mirrors the release script).
_HEARTBEAT_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


def turn_lock_key(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"turn:lock:{agent_id}:{chatroom_id}"


async def acquire_turn_lock(
    agent_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    *,
    ttl_s: int = DEFAULT_TURN_TTL_S,
    redis: Redis | None = None,
) -> str | None:
    """Return an opaque release token, or None if a turn is already running."""
    r = redis if redis is not None else get_redis()
    token = str(uuid.uuid4())
    got = await r.set(turn_lock_key(agent_id, chatroom_id), token, nx=True, ex=ttl_s)
    return token if got else None


async def release_turn_lock(
    agent_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    token: str,
    *,
    redis: Redis | None = None,
) -> None:
    r = redis if redis is not None else get_redis()
    await r.eval(_RELEASE_LUA, 1, turn_lock_key(agent_id, chatroom_id), token)


async def _heartbeat_loop(
    r: Redis,
    key: str,
    token: str,
    *,
    ttl_s: int,
    interval_s: float,
) -> None:
    """Refresh the lock TTL every ``interval_s`` while the turn body runs.

    A tool-heavy turn (stream rounds + sandbox tools + router quota waits) can
    legitimately outlive the static TTL; without this refresh a second turn
    would acquire the lapsed lock and run concurrently. Best-effort: a Redis
    hiccup skips one beat; a lost token (lock expired and was re-acquired)
    stops the loop — refreshing someone else's lock is never allowed.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            refreshed = await r.eval(_HEARTBEAT_LUA, 1, key, token, str(ttl_s * 1000))
        except Exception:
            _log.warning("turn lock heartbeat failed for %s", key, exc_info=True)
            continue
        if not refreshed:
            # Token gone or replaced — the lock is no longer ours to keep alive.
            return


@asynccontextmanager
async def turn_lock(
    agent_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    *,
    ttl_s: int = DEFAULT_TURN_TTL_S,
    redis: Redis | None = None,
    heartbeat_interval_s: float | None = None,
) -> AsyncIterator[bool]:
    """Async context manager yielding True if the lock was acquired, else False.

    On False the caller must NOT run a turn (one is already in flight). The
    lock is released only when *this* call acquired it. While the body runs a
    background heartbeat re-extends the TTL (every ``ttl_s / 3`` by default) so
    long turns cannot lose the lock mid-flight.
    """
    r = redis if redis is not None else get_redis()
    token = await acquire_turn_lock(agent_id, chatroom_id, ttl_s=ttl_s, redis=r)
    hb_task: asyncio.Task[None] | None = None
    if token is not None:
        hb_task = asyncio.create_task(
            _heartbeat_loop(
                r,
                turn_lock_key(agent_id, chatroom_id),
                token,
                ttl_s=ttl_s,
                interval_s=heartbeat_interval_s if heartbeat_interval_s is not None else ttl_s / 3,
            ),
            name=f"turn-lock-heartbeat:{agent_id}:{chatroom_id}",
        )
    try:
        yield token is not None
    finally:
        if hb_task is not None:
            hb_task.cancel()
            with suppress(asyncio.CancelledError):
                await hb_task
        if token is not None:
            await release_turn_lock(agent_id, chatroom_id, token, redis=r)


__all__ = [
    "DEFAULT_TURN_TTL_S",
    "acquire_turn_lock",
    "release_turn_lock",
    "turn_lock",
    "turn_lock_key",
]
