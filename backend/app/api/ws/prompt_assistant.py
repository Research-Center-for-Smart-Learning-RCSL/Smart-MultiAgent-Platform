"""`/ws/prompt-assistant/{session_id}` — stream assistant token deltas (§29).

Mirrors ``ws_workflow_runs``: ticket auth, then a resource-ownership check (the
session must belong to the authenticated user), then ``connection_loop`` fans
the session's Redis channel out to the socket. The worker publishes
``prompt.token`` / ``prompt.finished`` / ``prompt.error`` events.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from contexts.prompt_studio.infrastructure.channels import prompt_assistant_channel
from contexts.prompt_studio.infrastructure.session_store import SessionStore
from shared_kernel.auth.clients import get_redis
from shared_kernel.realtime import (
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)

router = APIRouter(tags=["ws"])


@router.websocket("/ws/prompt-assistant/{session_id}")
async def ws_prompt_assistant(ws: WebSocket, session_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return

    session = await SessionStore(get_redis()).get(session_id)
    if session is None:
        await ws.close(code=4404)
        return
    if session.user_id != auth.principal.user_id:
        await ws.close(code=4403)
        return

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[prompt_assistant_channel(session_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
    )


__all__ = ["router"]
