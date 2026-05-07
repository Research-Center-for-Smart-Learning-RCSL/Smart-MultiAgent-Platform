"""Process-wide async singletons used by the auth layer.

The auth layer needs three backends: Redis (session store + jti denylist +
sliding-window rate limiter), Vault (JWT sign/verify via Transit), and a
time source (mockable in tests). This module hides construction so handlers
and application services depend on *functions* rather than on importing the
specific client libraries — SoC win: swapping Vault for KMS or Redis for
Valkey is a one-file change.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from redis.asyncio import Redis

from app.config.settings import Settings, get_settings
from shared_kernel.infra.vault import VaultClient

_redis_lock: Final = threading.Lock()
_redis_instance: Redis | None = None
_vault_lock: Final = threading.Lock()
_vault_instance: VaultClient | None = None


# Time source — pytest fixtures can override via `set_clock`.
def _clock() -> datetime:
    return datetime.now(UTC)


def get_redis() -> Redis:
    """Return the shared async Redis client. Decoded to `str` by default."""
    global _redis_instance
    if _redis_instance is None:
        with _redis_lock:
            if _redis_instance is None:
                settings = get_settings()
                _redis_instance = Redis.from_url(
                    settings.redis.dsn,
                    decode_responses=True,
                )
    return _redis_instance


async def close_redis() -> None:
    """Lifespan shutdown hook."""
    global _redis_instance
    if _redis_instance is not None:
        await _redis_instance.aclose()
        _redis_instance = None


def get_vault_client(settings: Settings | None = None) -> VaultClient:
    global _vault_instance
    if _vault_instance is None:
        with _vault_lock:
            if _vault_instance is None:
                _vault_instance = VaultClient((settings or get_settings()).vault)
    return _vault_instance


def reset_for_tests() -> None:
    """Drop cached clients — called from pytest conftest on teardown."""
    global _redis_instance, _vault_instance
    _redis_instance = None
    _vault_instance = None


def now() -> datetime:
    return _clock()


def set_clock(fn: Callable[[], datetime]) -> None:
    global _clock
    _clock = fn


def monotonic() -> float:
    return time.monotonic()


__all__ = [
    "close_redis",
    "get_redis",
    "get_vault_client",
    "monotonic",
    "now",
    "reset_for_tests",
    "set_clock",
]
