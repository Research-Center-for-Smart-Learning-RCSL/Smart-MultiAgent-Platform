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
        Bucket.CHAT: Policy(60, s.chat_per_min_user, Scope.USER),
        Bucket.UPLOAD: Policy(60, s.upload_per_min_user, Scope.USER),
        Bucket.OTHER: Policy(60, s.other_per_min_user, Scope.USER),
    }


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
            return f"u:{user_id or 'anon'}"
        case Scope.IP:
            return f"i:{ip or 'unknown'}"
        case Scope.USER_AND_IP:
            return f"ui:{user_id or 'anon'}|{ip or 'unknown'}"
    return "x:unknown"


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
            _SCRIPT, 1, key,
            str(now_ms), str(window_ms), str(policy.max_count),
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


__all__ = ["Bucket", "Decision", "Policy", "Scope", "check", "default_policies"]
