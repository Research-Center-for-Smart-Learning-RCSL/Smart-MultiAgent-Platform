"""Generic distributed lock (Redis SET-NX + heartbeat + Lua release).

A configurable-key distributed lock extracted from the agent-specific
``turn_lock`` module.  Domain-specific key builders live in their
respective bounded contexts; this module only knows about Redis.
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

DEFAULT_LOCK_TTL_S = 300

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then " "return redis.call('del', KEYS[1]) else return 0 end"
)

_HEARTBEAT_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


async def acquire_lock(
    key: str,
    *,
    ttl_s: int = DEFAULT_LOCK_TTL_S,
    redis: Redis | None = None,
) -> str | None:
    """Return an opaque release token, or None if the lock is already held."""
    r = redis if redis is not None else get_redis()
    token = str(uuid.uuid4())
    got = await r.set(key, token, nx=True, ex=ttl_s)
    return token if got else None


async def release_lock(
    key: str,
    token: str,
    *,
    redis: Redis | None = None,
) -> None:
    r = redis if redis is not None else get_redis()
    await r.eval(_RELEASE_LUA, 1, key, token)


async def _heartbeat_loop(
    r: Redis,
    key: str,
    token: str,
    *,
    ttl_s: int,
    interval_s: float,
) -> None:
    """Refresh the lock TTL every ``interval_s`` while the body runs."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            refreshed = await r.eval(_HEARTBEAT_LUA, 1, key, token, str(ttl_s * 1000))
        except Exception:
            _log.warning("distributed lock heartbeat failed for %s", key, exc_info=True)
            continue
        if not refreshed:
            return


@asynccontextmanager
async def distributed_lock(
    key: str,
    *,
    ttl_s: int = DEFAULT_LOCK_TTL_S,
    redis: Redis | None = None,
    heartbeat_interval_s: float | None = None,
) -> AsyncIterator[bool]:
    """Async context manager yielding True if the lock was acquired.

    While the body runs a background heartbeat re-extends the TTL
    (every ``ttl_s / 3`` by default) so long operations cannot lose
    the lock mid-flight.
    """
    r = redis if redis is not None else get_redis()
    token = await acquire_lock(key, ttl_s=ttl_s, redis=r)
    hb_task: asyncio.Task[None] | None = None
    if token is not None:
        hb_task = asyncio.create_task(
            _heartbeat_loop(
                r,
                key,
                token,
                ttl_s=ttl_s,
                interval_s=heartbeat_interval_s if heartbeat_interval_s is not None else ttl_s / 3,
            ),
            name=f"distributed-lock-heartbeat:{key}",
        )
    try:
        yield token is not None
    finally:
        if hb_task is not None:
            hb_task.cancel()
            with suppress(asyncio.CancelledError):
                await hb_task
        if token is not None:
            await release_lock(key, token, redis=r)


__all__ = [
    "DEFAULT_LOCK_TTL_S",
    "acquire_lock",
    "distributed_lock",
    "release_lock",
]
