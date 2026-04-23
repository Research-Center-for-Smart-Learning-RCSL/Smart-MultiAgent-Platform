"""Cached IP-ban check — queried by the middleware on every request.

The table lives in Postgres (`ip_bans`) and the Admin CRUD lands in Phase I.
The middleware reads a Redis-backed cache invalidated on write so a freshly
added CIDR short-circuits the next request within ~5 s.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_CACHE_TTL_SEC = 5.0

SessionFactory = Callable[[], Awaitable[AsyncSession]]

# We intentionally go directly to the SQL table via the shared metadata
# registry rather than importing a context repository — `shared_kernel`
# sits below `contexts` and cannot depend on it. The DDL lives in
# `alembic/versions/0001_identity.py`; this file only reads back rows.


@dataclass(slots=True)
class _Cache:
    networks: list[ipaddress._BaseNetwork] = field(default_factory=list)
    loaded_at: float = 0.0


_cache = _Cache()


async def reload(db: AsyncSession) -> None:
    """Force refresh — invoked on INSERT/DELETE via the Admin handler."""
    # Query the ip_bans table directly with a raw SQL expression so this
    # module does not depend on any bounded context.
    rows = (await db.execute(sa.text("SELECT cidr FROM ip_bans"))).all()
    nets: list[ipaddress._BaseNetwork] = []
    for row in rows:
        raw = row[0]
        try:
            nets.append(ipaddress.ip_network(str(raw), strict=False))
        except ValueError:
            continue
    _cache.networks = nets
    _cache.loaded_at = time.monotonic()


def cache_is_fresh() -> bool:
    """Cheap check callers can use to avoid opening a DB session per request."""
    return time.monotonic() - _cache.loaded_at <= _CACHE_TTL_SEC


def is_banned_cached(ip: str) -> bool:
    """Pure cache lookup — callers must call `reload()` first when stale."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _cache.networks)


async def is_banned(db: AsyncSession, ip: str) -> bool:
    if not cache_is_fresh():
        await reload(db)
    return is_banned_cached(ip)


def invalidate() -> None:
    """Call after Admin writes so the next request reloads immediately."""
    _cache.loaded_at = 0.0


__all__ = ["invalidate", "is_banned", "reload"]
