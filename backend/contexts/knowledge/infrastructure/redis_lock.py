"""Redis-backed lock + snapshot stores for GraphRAG (R11a.01, R11.04).

- :class:`RedisBuildLockStore` → key ``graphrag:lock:{config_id}`` with a
  10-minute TTL (R11a.01), acquired via SET NX EX.
- :class:`RedisSnapshotStore` → key ``graphrag:build:{config_id}:{build_id}``
  holding a JSON-serialised subgraph snapshot with a 24h TTL.

Both consume the shared async Redis client via
:func:`shared_kernel.auth.clients.get_redis` so the process keeps a
single pool.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis

from shared_kernel.auth.clients import get_redis


def _lock_key(config_id: uuid.UUID) -> str:
    return f"graphrag:lock:{config_id}"


def _snap_key(config_id: uuid.UUID, build_id: uuid.UUID) -> str:
    return f"graphrag:build:{config_id}:{build_id}"


def _snap_prefix(config_id: uuid.UUID) -> str:
    return f"graphrag:build:{config_id}:"


class RedisBuildLockStore:
    """Acquire with SET NX EX for atomic lock + TTL (R11a.01)."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _r(self) -> Redis:
        return self._redis if self._redis is not None else get_redis()

    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        got = await self._r().set(_lock_key(config_id), "1", nx=True, ex=ttl_s)
        return bool(got)

    async def release(self, config_id: uuid.UUID) -> None:
        await self._r().delete(_lock_key(config_id))


class RedisSnapshotStore:
    """JSON-serialised Neo4j subgraph cache for compensation (R11.04)."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    def _r(self) -> Redis:
        return self._redis if self._redis is not None else get_redis()

    async def put(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        snapshot: dict[str, Any],
        ttl_s: int,
    ) -> None:
        await self._r().set(
            _snap_key(config_id, build_id),
            json.dumps(snapshot, default=str),
            ex=ttl_s,
        )

    async def get(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        raw = await self._r().get(_snap_key(config_id, build_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)  # type: ignore[no-any-return]

    async def delete(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
    ) -> None:
        await self._r().delete(_snap_key(config_id, build_id))

    async def scan_current(
        self,
        *,
        config_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Return the most recent cached build_id for a config, if any."""
        prefix = _snap_prefix(config_id)
        found: str | None = None
        async for raw in self._r().scan_iter(match=prefix + "*", count=16):
            key = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            found = key[len(prefix) :]
            break
        if found is None:
            return None
        try:
            return uuid.UUID(found)
        except ValueError:
            return None


__all__ = ["RedisBuildLockStore", "RedisSnapshotStore"]
