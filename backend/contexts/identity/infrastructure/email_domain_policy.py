"""Email-domain allow/deny policy (R19a.13).

Storage is a small Redis key (so the gate check is sub-ms) refreshed every 30 s,
which is also the worst-case lag between a change and its effect.

**There is no API that writes these keys.** The lists are set directly in Redis
today; `docs/operations.md` §7a.5 carries the operator recipe. An earlier version
of this docstring promised an Admin PATCH handler that was never built — if one
is added it must write exactly these three keys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from shared_kernel.auth.clients import get_redis

_KEY_ALLOW = "config:email_domain:allow"
_KEY_DENY = "config:email_domain:deny"
_KEY_MODE = "config:email_domain:mode"  # "allow" | "deny" | "off"


@dataclass(slots=True)
class _Cache:
    allow: set[str] = field(default_factory=set)
    deny: set[str] = field(default_factory=set)
    mode: str = "off"
    loaded_at: float = 0.0


_cache = _Cache()
_CACHE_TTL = 30.0


async def is_allowed(email: str) -> bool:
    domain = _domain_of(email)
    if not domain:
        return False
    await _refresh_if_stale()
    if _cache.mode == "deny" and domain in _cache.deny:
        return False
    if _cache.mode == "allow" and domain not in _cache.allow:  # noqa: SIM103 (guard-clause chain)
        return False
    return True


async def _refresh_if_stale() -> None:
    if time.monotonic() - _cache.loaded_at < _CACHE_TTL:
        return
    r = get_redis()
    async with r.pipeline(transaction=False) as pipe:
        pipe.smembers(_KEY_ALLOW)
        pipe.smembers(_KEY_DENY)
        pipe.get(_KEY_MODE)
        allow, deny, mode = await pipe.execute()
    _cache.allow = {d.lower() for d in allow}
    _cache.deny = {d.lower() for d in deny}
    _cache.mode = (mode or "off").lower() if isinstance(mode, str | bytes) else "off"  # type: ignore[assignment]
    _cache.loaded_at = time.monotonic()


def _domain_of(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].lower()


__all__ = ["is_allowed"]
