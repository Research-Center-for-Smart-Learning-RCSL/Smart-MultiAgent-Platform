"""`/ws/admin/tail` — Admin-only live audit feed (§22.14)."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from shared_kernel.realtime import (
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)
from shared_kernel.realtime.pubsub import AUDIT_TAIL_CHANNEL

router = APIRouter(tags=["ws"])


@router.websocket("/ws/admin/tail")
async def ws_admin_tail(ws: WebSocket) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return
    if not auth.principal.is_admin:
        await ws.close(code=4403)
        return
    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[AUDIT_TAIL_CHANNEL],
    )


__all__ = ["router"]
