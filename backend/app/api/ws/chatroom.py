"""`/ws/chatroom/{id}` — chatroom fan-out + presence (R13.19, §22.14)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, WebSocket

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.application.triggers import evaluate_presence_change
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom
from shared_kernel.db.session import async_session, get_sessionmaker
from shared_kernel.realtime import (
    ChannelConnection,
    PresenceTracker,
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)
from shared_kernel.realtime.pubsub import Publisher, room_channel

_log = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


async def _notify_presence(chatroom_id: uuid.UUID, *, has_live_users: bool) -> None:
    """Drive the silence-timer state for the room's bound agents (R15.05b).

    Best-effort and out-of-band of the WS connection's own session: a failure
    here must not drop the socket. Commits its own short-lived session because
    ``on_presence_changed`` may write audit rows in future."""
    try:
        async with async_session() as db:
            await evaluate_presence_change(db, chatroom_id=chatroom_id, has_live_users=has_live_users)
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

    async def on_open(conn: ChannelConnection) -> None:
        added = await presence.join(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
        )
        if added:
            await publisher.emit(
                "presence.joined",
                {"user_id": str(conn.principal.user_id)},
            )
            # Only on the empty→occupied transition: start bound agents' silence
            # timers once (R15.05b). Re-notifying on every joiner would keep
            # re-touching the timer and could stop a busy room ever going silent.
            members = await presence.list_room(chatroom_id)
            if len(members) == 1:
                await _notify_presence(chatroom_id, has_live_users=True)

    async def on_close(conn: ChannelConnection) -> None:
        left = await presence.leave(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
        )
        if left:
            await publisher.emit(
                "presence.left",
                {"user_id": str(conn.principal.user_id)},
            )
            # Only on the occupied→empty transition: pause silence timers once.
            remaining = await presence.list_room(chatroom_id)
            if not remaining:
                await _notify_presence(chatroom_id, has_live_users=False)

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[room_channel(chatroom_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
        on_open=on_open,
        on_close=on_close,
    )


__all__ = ["router"]
