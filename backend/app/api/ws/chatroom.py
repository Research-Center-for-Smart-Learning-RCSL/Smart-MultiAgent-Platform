"""`/ws/chatroom/{id}` — chatroom fan-out + presence (R13.19, §22.14)."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, WebSocket

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.application.triggers import evaluate_presence_change
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom
from contexts.conversation.interfaces import PresenceTracker, room_channel
from shared_kernel.db.session import async_session, get_sessionmaker
from shared_kernel.realtime import (
    ChannelConnection,
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


async def _notify_presence(chatroom_id: uuid.UUID) -> None:
    """Re-arm the silence timer for the room's bound agents on join (R15.05b).

    Join-edge only: the empty-room pause this used to also drive on leave was
    retired (2026-07-27-wakeup-sweep-failure-isolation C2) -- the live roster
    read in `evaluate_silence_trigger` is now the sole, level-triggered
    authority on an empty room. Best-effort and out-of-band of the WS
    connection's own session: a failure here must not drop the socket.
    Commits its own short-lived session because ``on_users_present`` may
    write audit rows in future."""
    try:
        async with async_session() as db:
            await evaluate_presence_change(db, chatroom_id=chatroom_id)
            await db.commit()
    except Exception:  # pragma: no cover — defensive; presence is fire-and-forget
        _log.warning("presence-change dispatch failed for room %s", chatroom_id, exc_info=True)


@router.websocket("/ws/chatroom/{chatroom_id}")
async def ws_chatroom(ws: WebSocket, chatroom_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return

    # ACL: reuse the same resolver the HTTP router uses so any change in
    # room-access rules is picked up in both channels at once.
    sm = get_sessionmaker()
    try:
        async with sm() as session, session.begin():
            access = await resolve_room_access(
                session,
                principal=auth.principal,
                chatroom_id=chatroom_id,
            )
            ensure_can_read(access, is_admin=auth.principal.is_admin)
    except (ChatroomNotFound, ForbiddenInRoom):
        await ws.close(code=4403)
        return

    presence = PresenceTracker()
    publisher = Publisher(room_channel(chatroom_id))

    _last_typing_ts: float = 0.0
    _typing_throttle_s: float = 2.0
    # F-18: typing is asserted per connection but was never retracted when that
    # connection ended, so a typist with a second tab open left the indicator
    # pinned for every other member — `presence.leave` reports left=False while
    # a sibling connection remains, which suppressed the only publish `on_close`
    # made, and nothing else expires typing state. This route body runs once per
    # connection, so the flag is correctly connection-scoped.
    _typing_active: bool = False

    async def _retract_typing(conn: ChannelConnection) -> None:
        """Publish `typing.stop` only once this user has no typing connection left.

        The event carries a user id and nothing else, so it is read as "this
        user stopped typing" by every client. Retracting on one connection's
        behalf would blank the indicator while a sibling tab is still mid-burst
        — and since the client sends `typing.start` once per burst rather than
        per keystroke, it would stay blank until the user paused and began a new
        one. The refcount lives in Redis because the two connections may be on
        different backend workers.
        """
        nonlocal _typing_active
        _typing_active = False
        last = await presence.typing_stop(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        if last:
            await publisher.emit("typing.stop", {"user_id": str(conn.principal.user_id)})

    async def on_client_message(conn: ChannelConnection, msg: dict) -> None:
        nonlocal _last_typing_ts, _typing_active
        msg_type = msg.get("type")
        if msg_type == "typing.start":
            now = time.monotonic()
            if now - _last_typing_ts < _typing_throttle_s:
                return
            _last_typing_ts = now
            await presence.typing_start(
                room_id=chatroom_id,
                user_id=conn.principal.user_id,
                connection_id=conn.connection_id,
            )
            # Emitted unconditionally rather than only on the first typing
            # connection: a client that joined mid-burst has no other way to
            # learn the indicator should be on, and a repeat start is idempotent
            # for every receiver.
            await publisher.emit(msg_type, {"user_id": str(conn.principal.user_id)})
            # Tracks what was actually published — a throttled start returns
            # above without emitting, so it must not arm the retraction.
            _typing_active = True
        elif msg_type == "typing.stop":
            await _retract_typing(conn)

    async def on_heartbeat(conn: ChannelConnection) -> None:
        # Every inbound frame proves the socket is alive — keep this user's
        # presence in the room from lapsing under a live connection (R13.19).
        await presence.heartbeat(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        if _typing_active:
            # A burst sends no periodic frame of its own, so the typing
            # assertion would otherwise lapse mid-burst on a long one.
            await presence.typing_heartbeat(
                room_id=chatroom_id,
                user_id=conn.principal.user_id,
                connection_id=conn.connection_id,
            )

    async def authorize(conn: ChannelConnection) -> bool:
        # Re-resolve room access mid-socket so a revoked guest link / lost
        # membership / tightened ACL tears the connection down (SEC-H2). Reads
        # the current (possibly refreshed) principal off `conn`.
        try:
            async with sm() as session, session.begin():
                access = await resolve_room_access(
                    session,
                    principal=conn.principal,
                    chatroom_id=chatroom_id,
                )
                ensure_can_read(access, is_admin=conn.principal.is_admin)
            return True
        except (ChatroomNotFound, ForbiddenInRoom):
            return False

    async def on_open(conn: ChannelConnection) -> None:
        added, roster_size = await presence.join(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        if added:
            await publisher.emit(
                "presence.joined",
                {"user_id": str(conn.principal.user_id)},
            )
            # FIX-03: atomic Lua guarantees exactly one concurrent first-joiner
            # observes roster_size == 1, so the transition fires exactly once.
            # Must stay nested under `added` — the roster SADD is idempotent,
            # so a second tab/reconnect of an already-present lone user would
            # also see roster_size == 1 and must NOT re-fire the transition.
            if roster_size == 1:
                await _notify_presence(chatroom_id)

    async def on_close(conn: ChannelConnection) -> None:
        nonlocal _typing_active
        left, _roster_size = await presence.leave(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        # F-18: a connection that goes away has stopped typing by definition.
        # Deliberately outside the `left` guard below — that guard is False
        # exactly in the case this fixes, where a sibling connection of the
        # same user keeps the roster entry alive. The retraction itself is
        # refcounted, so a sibling that is *also* typing keeps the indicator up.
        if _typing_active:
            await _retract_typing(conn)
        if left:
            await publisher.emit(
                "presence.left",
                {"user_id": str(conn.principal.user_id)},
            )

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[room_channel(chatroom_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
        on_open=on_open,
        on_close=on_close,
        on_client_message=on_client_message,
        on_heartbeat=on_heartbeat,
        authorize=authorize,
    )


__all__ = ["router"]
