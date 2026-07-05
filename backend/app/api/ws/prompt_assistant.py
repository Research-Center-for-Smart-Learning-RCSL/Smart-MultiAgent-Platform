"""`/ws/prompt-assistant/{session_id}` — stream assistant token deltas (§29).

Mirrors ``ws_workflow_runs``: ticket auth, then a resource-ownership check via
the context's facade (never infrastructure directly), then ``connection_loop``
fans the session's Redis channel out to the socket. The worker publishes
``prompt.token`` / ``prompt.finished`` / ``prompt.error`` events.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from contexts.prompt_studio.interfaces import prompt_assistant_channel
from contexts.prompt_studio.interfaces.facade import PromptStudioFacade
from shared_kernel.db.session import get_sessionmaker
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

    sm = get_sessionmaker()
    async with sm() as db:
        owned = await PromptStudioFacade(db).verify_session_owner(session_id, auth.principal.user_id)
    if not owned:
        # Collapses "no such session" and "wrong owner" into one code — never
        # leak session existence to a non-owner (mirrors the HTTP mapping of
        # SessionNotFound, which is 404 for both causes).
        await ws.close(code=4404)
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
