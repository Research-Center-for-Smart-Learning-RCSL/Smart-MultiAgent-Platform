"""Email-domain allow/deny policy (R19a.13).

The Admin tunes the lists at runtime. Storage is a small Redis key (so the
gate check is sub-ms) refreshed every 30 s. The Admin PATCH handler (Phase
I) writes the same keys.
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
    if _cache.mode == "allow" and domain not in _cache.allow:
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
    _cache.mode = (mode or "off").lower() if isinstance(mode, (str, bytes)) else "off"
    _cache.loaded_at = time.monotonic()


def _domain_of(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].lower()


__all__ = ["is_allowed"]
