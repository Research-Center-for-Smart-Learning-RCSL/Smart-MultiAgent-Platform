"""`/ws/workflow-runs/{id}` — workflow step transitions + approval prompts.

The workflow context publishes into `ws:wf:{run_id}` from Phase G; this
endpoint is the pass-through consumer. ACL is stubbed to "any project
member of the run's project" — refined once WorkflowFacade lands. For now
admin + authenticated callers are allowed, and cross-context ownership
checks hook in where the TODO-marker is.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from shared_kernel.realtime import (
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)
from shared_kernel.realtime.pubsub import workflow_channel

router = APIRouter(tags=["ws"])


@router.websocket("/ws/workflow-runs/{run_id}")
async def ws_workflow_runs(ws: WebSocket, run_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return
    # TODO[G]: replace with WorkflowFacade.ensure_run_readable(principal, run_id).
    # Until then, any authenticated user can subscribe — channel fan-out is
    # scoped by `run_id` so there is no broad leakage beyond the
    # workflow-ID guess surface, but production MUST tighten this.
    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[workflow_channel(run_id)],
    )


__all__ = ["router"]
