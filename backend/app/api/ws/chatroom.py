"""`/ws/chatroom/{id}` — chatroom fan-out + presence (R13.19, §22.14)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime import (
    ChannelConnection,
    PresenceTracker,
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)
from shared_kernel.realtime.pubsub import Publisher, room_channel

router = APIRouter(tags=["ws"])


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
        async with sm() as session:
            async with session.begin():
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
            room_id=chatroom_id, user_id=conn.principal.user_id,
        )
        if added:
            await publisher.emit(
                "presence.joined",
                {"user_id": str(conn.principal.user_id)},
            )

    async def on_close(conn: ChannelConnection) -> None:
        left = await presence.leave(
            room_id=chatroom_id, user_id=conn.principal.user_id,
        )
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
        on_open=on_open,
        on_close=on_close,
    )


__all__ = ["router"]
