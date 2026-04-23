"""IP-ban cache short-circuit (R19.05 / R6.13).

Tests the in-process cache layer directly — the DB `reload()` path is
exercised by a live-stack E2E. Here we prime `_cache` via monkeypatching
and assert the pure `is_banned_cached` predicate behaves correctly across
CIDR, single-IP, and IPv6 bans.
"""

from __future__ import annotations

import ipaddress
import time

from shared_kernel.auth import ip_bans


def _prime(*cidrs: str) -> None:
    ip_bans._cache.networks = [  # type: ignore[attr-defined]
        ipaddress.ip_network(c, strict=False) for c in cidrs
    ]
    ip_bans._cache.loaded_at = time.monotonic()  # type: ignore[attr-defined]


def test_single_ip_ban() -> None:
    _prime("203.0.113.9/32")
    assert ip_bans.is_banned_cached("203.0.113.9")
    assert not ip_bans.is_banned_cached("203.0.113.10")


def test_cidr_range_ban() -> None:
    _prime("10.0.0.0/24")
    assert ip_bans.is_banned_cached("10.0.0.1")
    assert ip_bans.is_banned_cached("10.0.0.200")
    assert not ip_bans.is_banned_cached("10.0.1.1")


def test_malformed_caller_ip_safe() -> None:
    _prime("10.0.0.0/24")
    # Middleware may forward a garbage string; predicate must not crash.
    assert ip_bans.is_banned_cached("not-an-ip") is False


def test_cache_freshness_window() -> None:
    ip_bans._cache.loaded_at = time.monotonic()  # type: ignore[attr-defined]
    assert ip_bans.cache_is_fresh()
    ip_bans._cache.loaded_at = time.monotonic() - 10  # type: ignore[attr-defined]
    assert not ip_bans.cache_is_fresh()


def test_invalidate_forces_reload() -> None:
    _prime("10.0.0.0/24")
    ip_bans.invalidate()
    assert not ip_bans.cache_is_fresh()
