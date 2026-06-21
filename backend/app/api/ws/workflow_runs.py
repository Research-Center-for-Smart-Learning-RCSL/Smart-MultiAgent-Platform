"""`/ws/workflow-runs/{id}` — workflow step transitions + approval prompts.

The workflow context publishes into `ws:wf:{run_id}` from Phase G; this
endpoint is the pass-through consumer. ACL: caller must be an admin or a
member of the project that owns the workflow run.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from contexts.tenancy.interfaces.facade import TenancyFacade
from contexts.workflow.interfaces import workflow_channel
from contexts.workflow.interfaces.facade import WorkflowFacade
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime import (
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)

router = APIRouter(tags=["ws"])


@router.websocket("/ws/workflow-runs/{run_id}")
async def ws_workflow_runs(ws: WebSocket, run_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return

    sm = get_sessionmaker()
    async with sm() as session:
        project_id = await WorkflowFacade(session).get_run_project_id(run_id)
        if project_id is None:
            await ws.close(code=4404)
            return
        if not auth.principal.is_admin and not await TenancyFacade(session).is_project_member(
            auth.principal.user_id, project_id
        ):
            await ws.close(code=4403)
            return

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[workflow_channel(run_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
    )


__all__ = ["router"]
