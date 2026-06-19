"""Per-process DEK cache with short TTL (D.7).

Extracted from ``provider_router`` to reduce that module's size and make the
cache independently testable.  The revocation listener
(``revocation_listener.py``) punches entries out via Redis pub/sub.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass


@dataclass
class _DekCacheEntry:
    plaintext: bytes
    loaded_at: float


class DekCache:
    """In-process cache for decrypted key material — bounded by a short TTL."""

    TTL_SECONDS = 60.0

    def __init__(self) -> None:
        self._entries: dict[uuid.UUID, _DekCacheEntry] = {}

    def get(self, key_id: uuid.UUID) -> bytes | None:
        entry = self._entries.get(key_id)
        if entry is None:
            return None
        if time.monotonic() - entry.loaded_at > self.TTL_SECONDS:
            self._entries.pop(key_id, None)
            return None
        return entry.plaintext

    def put(self, key_id: uuid.UUID, plaintext: bytes) -> None:
        self._entries[key_id] = _DekCacheEntry(plaintext, time.monotonic())

    def drop(self, key_id: uuid.UUID) -> None:
        self._entries.pop(key_id, None)

    def clear(self) -> None:
        self._entries.clear()


# Module singleton — the revocation listener punches entries out in response
# to Redis pub/sub; the provider router reads/writes on every call.
DEK_CACHE = DekCache()


__all__ = ["DEK_CACHE", "DekCache"]
