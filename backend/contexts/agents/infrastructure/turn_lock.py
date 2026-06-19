"""Per-(agent, chatroom) turn lock (K.2).

Agent-specific key builder and context manager on top of the generic
``shared_kernel.realtime.distributed_lock``.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from shared_kernel.realtime.distributed_lock import (
    DEFAULT_LOCK_TTL_S,
    distributed_lock,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis

DEFAULT_TURN_TTL_S = DEFAULT_LOCK_TTL_S


def turn_lock_key(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"turn:lock:{agent_id}:{chatroom_id}"


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

    Thin wrapper that builds the domain-specific key and delegates to the
    generic ``distributed_lock``.
    """
    key = turn_lock_key(agent_id, chatroom_id)
    async with distributed_lock(
        key,
        ttl_s=ttl_s,
        redis=redis,
        heartbeat_interval_s=heartbeat_interval_s,
    ) as acquired:
        yield acquired


__all__ = [
    "DEFAULT_TURN_TTL_S",
    "turn_lock",
    "turn_lock_key",
]
