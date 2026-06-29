"""Room & user presence sets (S22.14 / R13.19).

Presence is connection-aware so one user with multiple tabs counts once and
only fully leaves a room when the *last* of their connections closes:

  - `ws:presence:{room_id}`               -- user_ids currently connected
                                             (the room roster `list_room` reads)
  - `ws:presence:{room_id}:{user_id}:conns` -- SET of this user's live
                                             connection_ids in the room; its
                                             cardinality is the refcount and its
                                             TTL is the liveness signal
  - `ws:user:{user_id}:rooms`             -- inverse index so a sweep can drop a
                                             user from every room cheaply

The conns SET carries a TTL longer than the WS idle-timeout (so a live
connection — which must send a frame within that window or be reaped — always
refreshes it in time via :meth:`heartbeat`). A connection that dies without a
clean :meth:`leave` lets the conns SET expire; `scrub_stale_presence` then drops
the orphaned roster entry. Keeping this state in Redis (not Postgres) matches the
fire-and-forget nature of WS presence -- after a crash, stale entries cost at
most one TTL window of UI lag.
"""

from __future__ import annotations

import uuid
from typing import Final

from shared_kernel.auth.clients import get_redis

# Longer than the WS idle-timeout (connection.py `_IDLE_TIMEOUT_SECONDS` = 120s):
# a live connection sends at least one frame per idle window and refreshes the
# conns SET on each, so this TTL never lapses under it. A truly-dead connection's
# entry expires within one window and is scrubbed.
_CONN_TTL_SECONDS: Final = 150
_SET_TTL_SECONDS: Final = 300  # roster/reverse-index safety net (volatile-lru)


def _room_key(room_id: uuid.UUID) -> str:
    return f"ws:presence:{room_id}"


def _user_rooms_key(user_id: uuid.UUID) -> str:
    return f"ws:user:{user_id}:rooms"


def _conns_key(room_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return f"ws:presence:{room_id}:{user_id}:conns"


class PresenceTracker:
    async def join(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
    ) -> bool:
        """Record a connection's presence. Returns True only when this is the
        user's FIRST live connection in the room (caller publishes
        `presence.joined` and arms the silence timer in that case); False for a
        second tab so a multi-tab user is announced once."""
        r = get_redis()
        ck = _conns_key(room_id, user_id)
        await r.sadd(ck, str(connection_id))
        await r.expire(ck, _CONN_TTL_SECONDS)
        first = await r.scard(ck) == 1
        await r.sadd(_room_key(room_id), str(user_id))
        await r.sadd(_user_rooms_key(user_id), str(room_id))
        await r.expire(_room_key(room_id), _SET_TTL_SECONDS)
        await r.expire(_user_rooms_key(user_id), _SET_TTL_SECONDS)
        return first

    async def heartbeat(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
    ) -> None:
        """Refresh the TTLs that prove this connection is still alive. Called on
        every inbound WS frame so presence never lapses under a live socket."""
        r = get_redis()
        ck = _conns_key(room_id, user_id)
        # Re-assert membership in case the SET lapsed between frames.
        await r.sadd(ck, str(connection_id))
        await r.expire(ck, _CONN_TTL_SECONDS)
        await r.expire(_room_key(room_id), _SET_TTL_SECONDS)
        await r.expire(_user_rooms_key(user_id), _SET_TTL_SECONDS)

    async def leave(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
    ) -> bool:
        """Remove a connection. Returns True only when that was the user's LAST
        live connection in the room (caller publishes `presence.left` and pauses
        the silence timer); False while other tabs remain."""
        r = get_redis()
        ck = _conns_key(room_id, user_id)
        await r.srem(ck, str(connection_id))
        if await r.scard(ck) > 0:
            return False
        await r.delete(ck)
        await r.srem(_room_key(room_id), str(user_id))
        await r.srem(_user_rooms_key(user_id), str(room_id))
        return True

    async def list_room(self, room_id: uuid.UUID) -> list[uuid.UUID]:
        raw = await get_redis().smembers(_room_key(room_id))
        return [uuid.UUID(v) for v in raw]


async def scrub_stale_presence() -> int:
    """Reconcile roster SETs against per-user conns SETs (ASYNC-7).

    A connection that dies without a clean :meth:`PresenceTracker.leave` leaves
    its user in ``ws:presence:{room}`` and ``ws:user:{user}:rooms`` even though
    the user's ``ws:presence:{room}:{user}:conns`` SET has expired. This sweep
    walks every room roster and drops any member whose conns SET is gone,
    removing the matching back-reference too.

    Returns the number of stale ``(room, user)`` memberships removed. Idempotent
    and safe to run repeatedly -- invoked by the retention worker.
    """
    r = get_redis()
    removed = 0
    async for room_key in r.scan_iter(match="ws:presence:*", count=200):
        # `ws:presence:*` also matches the per-(room,user) conns keys
        # (`ws:presence:{room}:{user}:conns`, four ':' separators). A room roster
        # key has exactly two ':' separators; skip anything else.
        if room_key.count(":") != 2:
            continue
        room_id_str = room_key.split(":", 2)[2]
        for user_id_str in await r.smembers(room_key):
            conns_key = f"ws:presence:{room_id_str}:{user_id_str}:conns"
            if await r.exists(conns_key):
                continue
            # No live connection left -- drop the membership both ways.
            await r.srem(room_key, user_id_str)
            await r.srem(f"ws:user:{user_id_str}:rooms", room_id_str)
            removed += 1
    return removed


__all__ = ["PresenceTracker", "scrub_stale_presence"]
