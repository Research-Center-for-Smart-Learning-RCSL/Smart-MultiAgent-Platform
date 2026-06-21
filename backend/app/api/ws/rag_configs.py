"""`/ws/rag-configs/{id}` — document ingest status + per-doc progress.

Knowledge context publishes into `ws:rag:{config_id}`; ACL is "member of
the RAG config's project". We reuse the tenancy role resolver so the
check is consistent with HTTP endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket

from contexts.knowledge.interfaces import rag_channel
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import Scope
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime import (
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)

router = APIRouter(tags=["ws"])


@router.websocket("/ws/rag-configs/{config_id}")
async def ws_rag_configs(ws: WebSocket, config_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return

    if not auth.principal.is_admin:
        sm = get_sessionmaker()
        try:
            async with sm() as session, session.begin():
                facade = KnowledgeFacade(session)
                cfg = await facade.get_rag_config(config_id)
                if cfg is None:
                    await ws.close(code=4404)
                    return
                resolver = TenancyRoleResolver(session)
                roles = await resolver.roles_for(
                    auth.principal,
                    Scope(project_id=cfg.project_id),
                )
                if not roles:
                    await ws.close(code=4403)
                    return
        except Exception:  # pragma: no cover
            # If the knowledge facade is stubbed or missing the config lookup,
            # deny the subscription conservatively.
            await ws.close(code=4403)
            return

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[rag_channel(config_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
    )


__all__ = ["router"]
