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

import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from shared_kernel.auth.clients import get_redis

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

# Default TTL comfortably exceeds a normal turn; a turn that outlives it has
# almost certainly died, and the lock should free for the next trigger.
DEFAULT_TURN_TTL_S = 300

_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
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


@asynccontextmanager
async def turn_lock(
    agent_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    *,
    ttl_s: int = DEFAULT_TURN_TTL_S,
    redis: Redis | None = None,
) -> AsyncIterator[bool]:
    """Async context manager yielding True if the lock was acquired, else False.

    On False the caller must NOT run a turn (one is already in flight). The
    lock is released only when *this* call acquired it.
    """
    token = await acquire_turn_lock(agent_id, chatroom_id, ttl_s=ttl_s, redis=redis)
    try:
        yield token is not None
    finally:
        if token is not None:
            await release_turn_lock(agent_id, chatroom_id, token, redis=redis)


__all__ = [
    "DEFAULT_TURN_TTL_S",
    "acquire_turn_lock",
    "release_turn_lock",
    "turn_lock",
    "turn_lock_key",
]
