"""Redis-backed lock + snapshot stores for GraphRAG (R11a.01, R11.04).

- :class:`RedisBuildLockStore` → key ``graphrag:lock:{config_id}`` with a
  10-minute TTL (R11a.01), acquired via SET NX EX with a per-acquisition
  fencing token.
- :class:`RedisSnapshotStore` → key ``graphrag:build:{config_id}:{build_id}``
  holding a JSON-serialised subgraph snapshot with a 24h TTL, plus a
  ``graphrag:current_build:{config_id}`` pointer recording the in-flight
  build id authoritatively (audit C4).

Both consume the shared async Redis client via
:func:`shared_kernel.auth.clients.get_redis` so the process keeps a
single pool.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable
from typing import Any, cast

from redis.asyncio import Redis

from shared_kernel.auth.clients import get_redis

# Compare-and-delete so a lock is only released by the holder that set it.
# Without this, a build that overran its TTL (the lock had already expired and
# been re-acquired by a second build) would DELETE the *second* build's lock on
# release and let a third build start concurrently (audit C3).
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then " "return redis.call('del', KEYS[1]) else return 0 end"
)

# Token-checked TTL extension: only the current holder may refresh, so a build
# that has lost the lock (TTL expired, re-acquired by another build) gets a
# falsy result and can fail closed instead of writing concurrently (review #3).
_REFRESH_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"
)


def _lock_key(config_id: uuid.UUID) -> str:
    return f"graphrag:lock:{config_id}"


def _snap_key(config_id: uuid.UUID, build_id: uuid.UUID) -> str:
    return f"graphrag:build:{config_id}:{build_id}"


def _snap_prefix(config_id: uuid.UUID) -> str:
    return f"graphrag:build:{config_id}:"


def _current_key(config_id: uuid.UUID) -> str:
    return f"graphrag:current_build:{config_id}"


class RedisBuildLockStore:
    """Acquire with SET NX EX for atomic lock + TTL (R11a.01).

    Each acquisition mints a random fencing token, kept in-instance for the
    lifetime of the holder (one store instance per build, created by the
    worker). ``release`` does a token-checked compare-and-delete so an overran
    lock that was re-acquired by another build is never deleted by the original
    holder (audit C3).
    """

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis
        # config_id -> token held by THIS instance.
        self._tokens: dict[uuid.UUID, str] = {}

    def _r(self) -> Redis:
        return self._redis if self._redis is not None else get_redis()

    async def acquire(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        token = uuid.uuid4().hex
        got = await self._r().set(_lock_key(config_id), token, nx=True, ex=ttl_s)
        if got:
            self._tokens[config_id] = token
        return bool(got)

    async def release(self, config_id: uuid.UUID) -> None:
        token = self._tokens.pop(config_id, None)
        if token is None:
            return
        await cast("Awaitable[Any]", self._r().eval(_RELEASE_LUA, 1, _lock_key(config_id), token))

    async def refresh(self, config_id: uuid.UUID, *, ttl_s: int) -> bool:
        token = self._tokens.get(config_id)
        if token is None:
            return False
        res = await cast(
            "Awaitable[Any]",
            self._r().eval(_REFRESH_LUA, 1, _lock_key(config_id), token, str(ttl_s * 1000)),
        )
        return bool(res)


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

    async def set_current(
        self,
        *,
        config_id: uuid.UUID,
        build_id: uuid.UUID,
        ttl_s: int,
    ) -> None:
        """Record the in-flight build id authoritatively (audit C4).

        Written when the builder takes its pre-build snapshot and cleared on
        every terminal path. The reconciler reads this rather than guessing
        from a non-deterministic key scan.
        """
        await self._r().set(_current_key(config_id), str(build_id), ex=ttl_s)

    async def get_current(
        self,
        *,
        config_id: uuid.UUID,
    ) -> uuid.UUID | None:
        raw = await self._r().get(_current_key(config_id))
        if raw is None:
            return None
        val = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            return uuid.UUID(val)
        except ValueError:
            return None

    async def clear_current(
        self,
        *,
        config_id: uuid.UUID,
    ) -> None:
        await self._r().delete(_current_key(config_id))

    async def scan_current(
        self,
        *,
        config_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Fallback build-id probe via key scan (superseded by ``get_current``).

        Retained so adapters/tests that predate the authoritative pointer still
        resolve a build id. Non-deterministic when several snapshots exist; the
        reconciler prefers ``get_current`` (audit C4).
        """
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
