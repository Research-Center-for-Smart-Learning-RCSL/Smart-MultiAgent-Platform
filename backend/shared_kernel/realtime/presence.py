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


async def scrub_stale_presence() -> int:
    """Reconcile presence SETs against heartbeat keys (ASYNC-7).

    A connection that dies without a clean :meth:`PresenceTracker.leave` leaves
    its user in ``ws:presence:{room}`` and ``ws:user:{user}:rooms`` even though
    the 60 s heartbeat key has long expired. This sweep walks every room SET and
    drops any member whose ``ws:presence:{room}:{user}:hb`` key is gone, removing
    the matching back-reference too.

    Returns the number of stale ``(room, user)`` memberships removed. Idempotent
    and safe to run repeatedly — invoked by the retention worker.
    """
    r = get_redis()
    removed = 0
    async for room_key in r.scan_iter(match="ws:presence:*", count=200):
        # `ws:presence:*` also matches the per-member heartbeat keys
        # (`ws:presence:{room}:{user}:hb`). A room SET key has exactly two ':'
        # separators; skip anything else.
        if room_key.endswith(":hb") or room_key.count(":") != 2:
            continue
        room_id_str = room_key.split(":", 2)[2]
        for user_id_str in await r.smembers(room_key):
            # Key formats mirror _heartbeat_key / _user_rooms_key.
            hb_key = f"ws:presence:{room_id_str}:{user_id_str}:hb"
            if await r.exists(hb_key):
                continue
            # Heartbeat lapsed — drop the membership both ways.
            await r.srem(room_key, user_id_str)
            await r.srem(f"ws:user:{user_id_str}:rooms", room_id_str)
            removed += 1
    return removed


__all__ = ["PresenceTracker", "scrub_stale_presence"]
