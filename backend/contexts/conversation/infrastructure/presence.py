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


# Per-user conns SET: add/remove a connection and report cardinality atomically.
_CONN_JOIN_LUA = (
    "redis.call('SADD', KEYS[1], ARGV[1]) "
    "redis.call('EXPIRE', KEYS[1], ARGV[2]) "
    "return redis.call('SCARD', KEYS[1])"
)
_CONN_LEAVE_LUA = (
    "redis.call('SREM', KEYS[1], ARGV[1]) "
    "local n = redis.call('SCARD', KEYS[1]) "
    "if n == 0 then redis.call('DEL', KEYS[1]) end "
    "return n"
)

# FIX-03: Room roster mutation + cardinality in one atomic step so concurrent
# first-joins/last-leaves cannot both miss the transition edge.
_ROSTER_JOIN_LUA = (
    "redis.call('SADD', KEYS[1], ARGV[1]) "
    "redis.call('EXPIRE', KEYS[1], ARGV[2]) "
    "return redis.call('SCARD', KEYS[1])"
)
_ROSTER_LEAVE_LUA = "redis.call('SREM', KEYS[1], ARGV[1]) " "return redis.call('SCARD', KEYS[1])"


class PresenceTracker:
    async def join(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
    ) -> tuple[bool, int]:
        """Record a connection's presence.

        Returns ``(first_connection_of_user, roster_size_after)``.
        ``first_connection_of_user`` is True only when this is the user's FIRST
        live connection in the room; ``roster_size_after`` is the room roster
        cardinality AFTER the join (atomically read via Lua so exactly one
        concurrent first-joiner observes ``roster_size_after == 1``).
        """
        r = get_redis()
        ck = _conns_key(room_id, user_id)
        size = await r.eval(_CONN_JOIN_LUA, 1, ck, str(connection_id), str(_CONN_TTL_SECONDS))
        first = int(size) == 1
        rk = _room_key(room_id)
        roster_size = int(await r.eval(_ROSTER_JOIN_LUA, 1, rk, str(user_id), str(_SET_TTL_SECONDS)))
        pipe = r.pipeline(transaction=False)
        pipe.sadd(_user_rooms_key(user_id), str(room_id))
        pipe.expire(_user_rooms_key(user_id), _SET_TTL_SECONDS)
        await pipe.execute()
        return first, roster_size

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
        # Re-assert membership in case the SET lapsed between frames. Runs on
        # every inbound frame, so pipeline the independent writes to one RTT.
        pipe = r.pipeline(transaction=False)
        pipe.sadd(ck, str(connection_id))
        pipe.expire(ck, _CONN_TTL_SECONDS)
        pipe.expire(_room_key(room_id), _SET_TTL_SECONDS)
        pipe.expire(_user_rooms_key(user_id), _SET_TTL_SECONDS)
        await pipe.execute()

    async def leave(
        self,
        *,
        room_id: uuid.UUID,
        user_id: uuid.UUID,
        connection_id: uuid.UUID,
    ) -> tuple[bool, int]:
        """Remove a connection.

        Returns ``(last_connection_of_user, roster_size_after)``.
        ``last_connection_of_user`` is True only when that was the user's LAST
        live connection in the room; ``roster_size_after`` is the room roster
        cardinality AFTER the leave (atomically read via Lua so exactly one
        concurrent last-leaver observes ``0``).
        """
        r = get_redis()
        ck = _conns_key(room_id, user_id)
        remaining = await r.eval(_CONN_LEAVE_LUA, 1, ck, str(connection_id))
        if int(remaining) > 0:
            return False, -1  # -1 sentinel: roster unchanged, caller ignores
        rk = _room_key(room_id)
        roster_size = int(await r.eval(_ROSTER_LEAVE_LUA, 1, rk, str(user_id)))
        pipe = r.pipeline(transaction=False)
        pipe.srem(_user_rooms_key(user_id), str(room_id))
        await pipe.execute()
        return True, roster_size

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
