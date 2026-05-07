"""Room & user presence sets (§22.14 / R13.19).

Two Redis SETs per room:

  - `ws:presence:{room_id}`       — user_ids currently connected
  - `ws:user:{user_id}:rooms`     — inverse index so `on_disconnect`
                                     cheaply removes the user from every
                                     room without scanning keys.

The reverse index is maintained alongside the forward set so a crashed
connection's membership falls out naturally when the pod terminates —
`join` sets a heartbeat key with a 60s TTL; a helper in the retention
worker scrubs entries whose heartbeat expired. Keeping this state in Redis
(not Postgres) matches the fire-and-forget nature of WS presence — after a
crash, stale entries cost at most one heartbeat window of UI lag.
"""

from __future__ import annotations

import uuid
from typing import Final

from shared_kernel.auth.clients import get_redis

_HEARTBEAT_TTL_SECONDS: Final = 60


def _room_key(room_id: uuid.UUID) -> str:
    return f"ws:presence:{room_id}"


def _user_rooms_key(user_id: uuid.UUID) -> str:
    return f"ws:user:{user_id}:rooms"


def _heartbeat_key(room_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"ws:presence:{room_id}:{user_id}:hb"


class PresenceTracker:
    async def join(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Record presence. Returns True if the user was newly added to the
        room (caller should publish a `presence.joined` event in that
        case), False if already present."""
        r = get_redis()
        added = await r.sadd(_room_key(room_id), str(user_id))
        await r.sadd(_user_rooms_key(user_id), str(room_id))
        await r.set(_heartbeat_key(room_id, user_id), "1", ex=_HEARTBEAT_TTL_SECONDS)
        return bool(added)

    async def heartbeat(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        await get_redis().set(
            _heartbeat_key(room_id, user_id),
            "1",
            ex=_HEARTBEAT_TTL_SECONDS,
        )

    async def leave(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        """Remove presence. Returns True if the user was actually present."""
        r = get_redis()
        removed = await r.srem(_room_key(room_id), str(user_id))
        await r.srem(_user_rooms_key(user_id), str(room_id))
        await r.delete(_heartbeat_key(room_id, user_id))
        return bool(removed)

    async def list_room(self, room_id: uuid.UUID) -> list[uuid.UUID]:
        raw = await get_redis().smembers(_room_key(room_id))
        return [uuid.UUID(v) for v in raw]


__all__ = ["PresenceTracker"]
