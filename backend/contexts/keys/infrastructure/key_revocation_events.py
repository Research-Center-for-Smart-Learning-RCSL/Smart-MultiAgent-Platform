"""Redis pub/sub channels for key revocation fanout (D.5 / D.7).

The router caches unwrapped DEKs in-process for the life of a call. When a
key is deleted or a carry is withdrawn, the cache has to drop the entry
across every app worker -- not just the one that served the delete request.
Redis pub/sub is the cheapest multi-process invalidation bus we have, and
it matches S7.4's "in-flight calls complete but no new calls issued" rule
because the cache check happens *before* each outbound call, not once at
the start of a chat turn.

Two channels, two meanings:
- ``key.revoked`` -- the key row itself was soft-deleted (D.4).
- ``key.carry_revoked`` -- a `key_projects` carry was withdrawn (D.5 / R7.04).

Subscribers (the router in D.7) treat both the same way: drop any DEK
cached under the key id. They're split so audit / observability can
distinguish the two cases.
"""

from __future__ import annotations

import uuid
from typing import Final

from shared_kernel.auth.clients import get_redis

CHANNEL_KEY_REVOKED: Final = "key.revoked"
CHANNEL_KEY_CARRY_REVOKED: Final = "key.carry_revoked"


async def publish_key_revoked(key_id: uuid.UUID) -> None:
    """Announce that `key_id` is no longer usable."""
    await get_redis().publish(CHANNEL_KEY_REVOKED, str(key_id))


async def publish_carry_revoked(*, key_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Announce that `key_id` was withdrawn from `project_id`."""
    payload = f"{key_id}:{project_id}"
    await get_redis().publish(CHANNEL_KEY_CARRY_REVOKED, payload)


__all__ = [
    "CHANNEL_KEY_CARRY_REVOKED",
    "CHANNEL_KEY_REVOKED",
    "publish_carry_revoked",
    "publish_key_revoked",
]
