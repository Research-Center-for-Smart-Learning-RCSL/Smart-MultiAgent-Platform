"""Generic distributed lock (Redis SET-NX + heartbeat + Lua release).

A configurable-key distributed lock extracted from the agent-specific
``turn_lock`` module.  Domain-specific key builders live in their
respective bounded contexts; this module only knows about Redis.

The heartbeat used to fail silently in both of its failure modes: an exception
cost a whole refresh interval and then carried on, and a refresh the server
rejected simply ended the loop. Either way the body ran on believing it still
held a lock that had lapsed, which for a caller whose job timeout outlives the
TTL means two holders doing the same work at once. The context manager now
yields a :class:`LockHandle` whose ``held`` goes false in both cases, so a
long-running body can check it and fail closed.
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

# Consecutive refresh errors before the lock is declared lost. More than one,
# because a single Redis blip must not convert a healthy body into a failed one;
# small, because every failure spends TTL the body cannot see.
HEARTBEAT_MAX_FAILURES = 3

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then " "return redis.call('del', KEYS[1]) else return 0 end"
)

_HEARTBEAT_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


class LockHandle:
    """What :func:`distributed_lock` yields: the acquisition, plus liveness.

    ``bool(handle)`` answers "did we get the lock", which is what every existing
    call site asks at the top of the block and what keeps this a drop-in for the
    bare ``bool`` it replaces. ``held`` answers the different question "do we
    still have it", and only that one can change while the body runs.

    Deliberately not an ``asyncio.Event``: the consumers poll this at a round
    boundary rather than awaiting it, and an Event would invite a design where a
    lock loss interrupts the body at an arbitrary await.
    """

    __slots__ = ("_acquired", "_lost_reason")

    def __init__(self, *, acquired: bool) -> None:
        self._acquired = acquired
        self._lost_reason: str | None = None

    def __bool__(self) -> bool:
        return self._acquired

    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def held(self) -> bool:
        return self._acquired and self._lost_reason is None

    @property
    def lost_reason(self) -> str | None:
        return self._lost_reason

    def mark_lost(self, reason: str) -> None:
        if self._lost_reason is None:
            self._lost_reason = reason


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
    handle: LockHandle,
) -> None:
    """Refresh the lock TTL every ``interval_s`` while the body runs.

    A failed refresh is retried *inside* the interval rather than after another
    full one. At the default ``ttl_s / 3`` cadence, waiting a whole interval per
    failure means three failures spend exactly the TTL — the lock is gone before
    the third attempt is even made.
    """
    failures = 0
    retry_delay = interval_s / (HEARTBEAT_MAX_FAILURES + 1)
    while True:
        await asyncio.sleep(interval_s if failures == 0 else retry_delay)
        try:
            refreshed = await r.eval(_HEARTBEAT_LUA, 1, key, token, str(ttl_s * 1000))
        except Exception:
            failures += 1
            _log.warning(
                "distributed lock heartbeat failed for %s (%d/%d)",
                key,
                failures,
                HEARTBEAT_MAX_FAILURES,
                exc_info=True,
            )
            if failures >= HEARTBEAT_MAX_FAILURES:
                handle.mark_lost("heartbeat_unreachable")
                return
            continue
        failures = 0
        if not refreshed:
            # The compare-and-pexpire found a different token or no key: our
            # lock expired and may already have been re-taken. Ending the loop
            # quietly here is what used to let two bodies run at once.
            _log.warning("distributed lock %s was lost before its body finished", key)
            handle.mark_lost("refresh_rejected")
            return


@asynccontextmanager
async def distributed_lock(
    key: str,
    *,
    ttl_s: int = DEFAULT_LOCK_TTL_S,
    redis: Redis | None = None,
    heartbeat_interval_s: float | None = None,
) -> AsyncIterator[LockHandle]:
    """Async context manager yielding a handle, truthy if the lock was acquired.

    While the body runs a background heartbeat re-extends the TTL (every
    ``ttl_s / 3`` by default). A body that outlives its TTL is possible whenever
    the caller's own timeout does, so the handle's ``held`` is the signal for
    such a body to stop rather than run beside a second holder.
    """
    r = redis if redis is not None else get_redis()
    token = await acquire_lock(key, ttl_s=ttl_s, redis=r)
    handle = LockHandle(acquired=token is not None)
    hb_task: asyncio.Task[None] | None = None
    if token is not None:
        hb_task = asyncio.create_task(
            _heartbeat_loop(
                r,
                key,
                token,
                ttl_s=ttl_s,
                interval_s=heartbeat_interval_s if heartbeat_interval_s is not None else ttl_s / 3,
                handle=handle,
            ),
            name=f"distributed-lock-heartbeat:{key}",
        )
    try:
        yield handle
    finally:
        if hb_task is not None:
            hb_task.cancel()
            with suppress(asyncio.CancelledError):
                await hb_task
        if token is not None:
            # Unconditional, and safe even after a loss: the release script is a
            # compare-and-delete on our own token, so a key another holder now
            # owns is left alone.
            await release_lock(key, token, redis=r)


__all__ = [
    "DEFAULT_LOCK_TTL_S",
    "HEARTBEAT_MAX_FAILURES",
    "LockHandle",
    "acquire_lock",
    "distributed_lock",
    "release_lock",
]
