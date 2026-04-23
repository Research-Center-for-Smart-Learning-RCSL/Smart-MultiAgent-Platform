"""Redis-backed login-attempt + email-domain policy (R6.04, R19a.13).

- Per-account: 5 failed logins / 15 min → 15 min lockout.
- Per-IP: 20 failed logins / 15 min → 15 min lockout.

Both counters live in Redis with fixed 15-minute TTL windows. On successful
login the *account* counter is cleared (the IP counter is not, because an
attacker brute-forcing from a single IP against many accounts mustn't be
able to reset by guessing one right).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from shared_kernel.auth.clients import get_redis

_WINDOW: Final = timedelta(minutes=15)
_ACCOUNT_THRESHOLD: Final = 5
_IP_THRESHOLD: Final = 20
_ACCOUNT_PREFIX: Final = "auth:lockout:acct:"
_IP_PREFIX: Final = "auth:lockout:ip:"


@dataclass(frozen=True, slots=True)
class LockoutState:
    locked: bool
    retry_after_seconds: int


async def check_and_record_failure(email: str, ip: str) -> LockoutState:
    """Increment both counters atomically; return lockout state."""
    r = get_redis()
    akey = _ACCOUNT_PREFIX + email.lower()
    ikey = _IP_PREFIX + ip
    async with r.pipeline(transaction=True) as pipe:
        pipe.incr(akey)
        pipe.expire(akey, int(_WINDOW.total_seconds()))
        pipe.incr(ikey)
        pipe.expire(ikey, int(_WINDOW.total_seconds()))
        acount, _, icount, _ = await pipe.execute()

    if acount >= _ACCOUNT_THRESHOLD or icount >= _IP_THRESHOLD:
        # Extend the lock by the remaining window so the lockout fires for the
        # configured 15 min from the *last* failure.
        ttl = await r.ttl(akey if acount >= _ACCOUNT_THRESHOLD else ikey)
        retry = max(1, ttl) if ttl > 0 else int(_WINDOW.total_seconds())
        return LockoutState(locked=True, retry_after_seconds=retry)
    return LockoutState(locked=False, retry_after_seconds=0)


async def check_only(email: str, ip: str) -> LockoutState:
    r = get_redis()
    akey = _ACCOUNT_PREFIX + email.lower()
    ikey = _IP_PREFIX + ip
    raw_a = await r.get(akey)
    raw_i = await r.get(ikey)
    a = int(raw_a) if raw_a else 0
    i = int(raw_i) if raw_i else 0
    if a >= _ACCOUNT_THRESHOLD:
        ttl = await r.ttl(akey)
        return LockoutState(locked=True, retry_after_seconds=max(1, ttl))
    if i >= _IP_THRESHOLD:
        ttl = await r.ttl(ikey)
        return LockoutState(locked=True, retry_after_seconds=max(1, ttl))
    return LockoutState(locked=False, retry_after_seconds=0)


async def clear_account(email: str) -> None:
    """Called on a successful login — do NOT touch the IP counter."""
    await get_redis().delete(_ACCOUNT_PREFIX + email.lower())


__all__ = ["LockoutState", "check_and_record_failure", "check_only", "clear_account"]
