"""`/ws/project/{id}` -- project-level dashboard events ([R33.03]-[R33.04]).

Authenticates via existing WS auth, verifies project membership, subscribes
to `ws:project:{project_id}`. The `authorize` callback re-checks membership
on the auth watchdog interval so a revoked member is disconnected.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from contexts.activities.infrastructure.channels import project_channel
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime import (
    ChannelConnection,
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)

router = APIRouter(tags=["ws"])


@router.websocket("/ws/project/{project_id}")
async def ws_project(ws: WebSocket, project_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return

    sm = get_sessionmaker()
    async with sm() as session:
        if not auth.principal.is_admin and not await TenancyFacade(session).is_project_member(
            auth.principal.user_id, project_id
        ):
            await ws.close(code=4403)
            return

    async def authorize(conn: ChannelConnection) -> bool:
        if conn.principal.is_admin:
            return True
        async with sm() as session:
            return await TenancyFacade(session).is_project_member(conn.principal.user_id, project_id)

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[project_channel(project_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
        authorize=authorize,
    )


__all__ = ["router"]
