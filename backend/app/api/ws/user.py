"""`/ws/user/{user_id}` — per-user notifications / ban-kick (§22.14)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from contexts.identity.interfaces import user_channel
from shared_kernel.realtime import (
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)

router = APIRouter(tags=["ws"])


@router.websocket("/ws/user/{user_id}")
async def ws_user(ws: WebSocket, user_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        # 4401 is the app-level "unauthorised" code used across all /ws/*
        # endpoints so the client can react uniformly to token rejects.
        await ws.close(code=4401)
        return
    if auth.principal.user_id != user_id and not auth.principal.is_admin:
        # A user may only subscribe to their own channel; admins can tail
        # any user's stream (support / incident response).
        await ws.close(code=4403)
        return
    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[user_channel(user_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
    )


__all__ = ["router"]
