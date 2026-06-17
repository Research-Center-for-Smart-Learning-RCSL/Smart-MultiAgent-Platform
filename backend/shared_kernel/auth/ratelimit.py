"""Sliding-window rate limiter in Redis + Lua (R19.02, R19.04, R19.06).

Four default buckets — see §19 and `settings.limits`. The worker moves to a
1-second granularity sliding window implemented as a Lua script executed
atomically so under concurrent requests we never over-count.

For each incoming request the middleware resolves:
  * `bucket` — chosen by path prefix (rate_limit_policies row).
  * `identity` — depending on scope: `{user_id}`, `{actor_ip}`, or
    `{user_id}|{actor_ip}` concatenation.

Runtime overrides in `rate_limit_policies` row live in Postgres and are
mirrored into Redis by a bootstrap / admin update (to keep the hot path off
the DB). The mirror key is `config:ratelimit:{bucket}` — if unset we fall
back to compile-time defaults.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Final

from redis.asyncio import Redis

from app.config.settings import Settings, get_settings
from shared_kernel.auth.clients import get_redis
from shared_kernel.observability.metrics import REDIS_COMMAND_ERRORS

# Lua script — atomic increment + TTL + capacity check in O(log N).
# Window implementation: sorted set of request timestamps (milliseconds).
# Expire old entries with ZREMRANGEBYSCORE; count remaining; if ≥ max, deny
# and report retry-after = (oldest entry + window) - now.
_SCRIPT: Final = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_count = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= max_count then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local oldest_ms = tonumber(oldest[2])
  local reset_ms = (oldest_ms or now_ms) + window_ms
  return {0, count, reset_ms}
end
redis.call('ZADD', key, now_ms, tostring(now_ms) .. ':' .. tostring(math.random(1,100000)))
redis.call('PEXPIRE', key, window_ms)
return {1, count + 1, now_ms + window_ms}
"""


class Bucket(str, Enum):
    AUTH = "auth"
    # API-9: account-recovery flows (password-reset / verify-email) get their
    # own IP counter so reset-flooding cannot starve the login bucket and
    # shared-NAT users blocked on recovery can still log in.
    AUTH_RECOVERY = "auth-recovery"
    CHAT = "chat-send"
    UPLOAD = "upload"
    OTHER = "other"


class Scope(str, Enum):
    USER = "user"
    IP = "ip"
    USER_AND_IP = "user_and_ip"


@dataclass(frozen=True, slots=True)
class Policy:
    window_sec: int
    max_count: int
    scope: Scope


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    remaining: int
    limit: int
    reset_ms: int
    retry_after_seconds: int


def default_policies(settings: Settings | None = None) -> dict[Bucket, Policy]:
    s = (settings or get_settings()).limits
    return {
        Bucket.AUTH: Policy(60, s.auth_per_min_ip, Scope.IP),
        # Same per-IP allowance as AUTH but a separate sliding window.
        Bucket.AUTH_RECOVERY: Policy(60, s.auth_per_min_ip, Scope.IP),
        Bucket.CHAT: Policy(60, s.chat_per_min_user, Scope.USER),
        Bucket.UPLOAD: Policy(60, s.upload_per_min_user, Scope.USER),
        Bucket.OTHER: Policy(60, s.other_per_min_user, Scope.USER),
    }


async def mirror_policy(key: str, *, window_sec: int, max_count: int, scope: str) -> None:
    """Write one policy into the limiter's hot-path Redis mirror.

    Single source of truth for the ``config:ratelimit:{key}`` hash shape, shared
    by startup priming and the admin PATCH so the two can never write a different
    field set. ``_resolve_policy`` reads exactly these fields.
    """
    await get_redis().hset(
        f"config:ratelimit:{key}",
        mapping={"window_sec": int(window_sec), "max_count": int(max_count), "scope": scope},
    )


async def prime_policies() -> None:
    """Seed the DB policy rows from compile-time defaults and mirror them into
    Redis. Run once at app startup so:

      * ``GET``/``PATCH /api/admin/rate-limits`` operate on real rows (the table
        ships empty, so without this the GET returns ``[]`` and the PATCH 404s);
      * the limiter's hot path reads the ``config:ratelimit:{bucket}`` mirror
        rather than the DB;
      * an operator override (persisted in the DB row) survives a Redis flush —
        the DB row is authoritative and ``ON CONFLICT DO NOTHING`` never clobbers
        an override on a later boot.
    """
    from sqlalchemy import text

    from shared_kernel.db.session import async_session

    defaults = default_policies()
    async with async_session() as db, db.begin():
        for bucket, pol in defaults.items():
            await db.execute(
                text(
                    "INSERT INTO rate_limit_policies (key, window_sec, max_count, scope) "
                    "VALUES (:k, :w, :m, :s) ON CONFLICT (key) DO NOTHING"
                ).bindparams(k=bucket.value, w=pol.window_sec, m=pol.max_count, s=pol.scope.value)
            )
        rows = (
            await db.execute(text("SELECT key, window_sec, max_count, scope FROM rate_limit_policies"))
        ).all()

    bucket_keys = {b.value for b in Bucket}
    for key, window_sec, max_count, scope in rows:
        if key not in bucket_keys:
            continue  # e.g. advisory_* rows share this table; not limiter buckets
        await mirror_policy(key, window_sec=window_sec, max_count=max_count, scope=scope)


async def _resolve_policy(bucket: Bucket) -> Policy:
    """Runtime override via `config:ratelimit:{bucket}` Redis key, else default."""
    r = get_redis()
    try:
        raw = await r.hgetall(f"config:ratelimit:{bucket.value}")
    except Exception:
        REDIS_COMMAND_ERRORS.labels(command="hgetall").inc()
        raise
    if raw:
        try:
            return Policy(
                window_sec=int(raw["window_sec"]),
                max_count=int(raw["max_count"]),
                scope=Scope(raw["scope"]),
            )
        except (KeyError, ValueError):
            pass
    return default_policies()[bucket]


def _identity(scope: Scope, *, user_id: str | None, ip: str | None) -> str:
    match scope:
        case Scope.USER:
            # S8: unauthenticated requests must not share a single bucket;
            # include the client IP so each anonymous source is rate-limited
            # independently.
            if user_id:
                return f"u:{user_id}"
            return f"u:anon:{ip or 'unknown'}"
        case Scope.IP:
            return f"i:{ip or 'unknown'}"
        case Scope.USER_AND_IP:
            return f"ui:{user_id or 'anon'}|{ip or 'unknown'}"
    return "x:unknown"  # type: ignore[unreachable]


async def check(
    *,
    bucket: Bucket,
    user_id: str | None,
    actor_ip: str | None,
) -> Decision:
    policy = await _resolve_policy(bucket)
    ident = _identity(policy.scope, user_id=user_id, ip=actor_ip)
    key = f"rl:{bucket.value}:{ident}"
    r: Redis = get_redis()
    now_ms = int(time.time() * 1000)
    window_ms = policy.window_sec * 1000
    try:
        raw = await r.eval(
            _SCRIPT,
            1,
            key,
            str(now_ms),
            str(window_ms),
            str(policy.max_count),
        )
    except Exception:
        REDIS_COMMAND_ERRORS.labels(command="eval").inc()
        raise
    allowed = bool(int(raw[0]))
    count = int(raw[1])
    reset_ms = int(raw[2])
    remaining = max(0, policy.max_count - count)
    retry_after = 0 if allowed else max(1, (reset_ms - now_ms) // 1000 + 1)
    return Decision(
        allowed=allowed,
        remaining=remaining,
        limit=policy.max_count,
        reset_ms=reset_ms,
        retry_after_seconds=retry_after,
    )


async def check_raw(*, key: str, window_sec: int, max_count: int) -> Decision:
    """Rate-check against an arbitrary Redis key with an inline fixed policy.

    Bypasses the Postgres-mirrored config lookup; intended for application-layer
    rate limits (e.g., per-email password-reset) that sit outside the four global
    middleware buckets.
    """
    r: Redis = get_redis()
    now_ms = int(time.time() * 1000)
    window_ms = window_sec * 1000
    try:
        raw = await r.eval(
            _SCRIPT,
            1,
            key,
            str(now_ms),
            str(window_ms),
            str(max_count),
        )
    except Exception:
        REDIS_COMMAND_ERRORS.labels(command="eval").inc()
        raise
    allowed = bool(int(raw[0]))
    count = int(raw[1])
    reset_ms = int(raw[2])
    remaining = max(0, max_count - count)
    retry_after = 0 if allowed else max(1, (reset_ms - now_ms) // 1000 + 1)
    return Decision(
        allowed=allowed,
        remaining=remaining,
        limit=max_count,
        reset_ms=reset_ms,
        retry_after_seconds=retry_after,
    )


__all__ = ["Bucket", "Decision", "Policy", "Scope", "check", "check_raw", "default_policies"]
