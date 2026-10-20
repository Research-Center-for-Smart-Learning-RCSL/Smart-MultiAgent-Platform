"""`/ws/project/{id}` -- project-level dashboard events ([R33.03]-[R33.04]).

Authenticates via existing WS auth, verifies project membership, subscribes
to `ws:project:{project_id}`. The `authorize` callback re-checks membership
on the auth watchdog interval so a revoked member is disconnected.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from app.api.v1.deps import has_project_role
from contexts.activities.infrastructure.channels import project_channel
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime import (
    AccessOutcome,
    ChannelConnection,
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
    refuse_after_accept,
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
        project = await TenancyFacade(session).get_project(project_id)
        if project is None:
            await refuse_after_accept(ws, auth.subprotocol, AccessOutcome.GONE)
            return
        if not await has_project_role(db=session, principal=auth.principal, project_id=project_id):
            await refuse_after_accept(ws, auth.subprotocol, AccessOutcome.FORBIDDEN)
            return

    async def authorize(conn: ChannelConnection) -> AccessOutcome:
        async with sm() as session:
            project = await TenancyFacade(session).get_project(project_id)
            if project is None:
                return AccessOutcome.GONE
            if await has_project_role(db=session, principal=conn.principal, project_id=project_id):
                return AccessOutcome.ALLOWED
            return AccessOutcome.FORBIDDEN

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[project_channel(project_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
        authorize=authorize,
        authorize_every_n_ticks=1,
    )


__all__ = ["router"]
