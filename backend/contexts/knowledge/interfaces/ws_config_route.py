"""Shared WS-route factory for a project-scoped, config-gated live channel.

``/ws/graphrag/{id}``, ``/ws/rag-configs/{id}``, and ``/ws/knowmap/{id}`` used
to each hand-roll an identical scaffold: subprotocol auth, then (unless admin)
a facade config lookup + project-membership check, then subscribe to a
per-domain Redis channel. Collapsed here (code review, 2026-07-10) so the
auth/close-code flow has one implementation instead of three copies that must
be kept in sync by hand.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, WebSocket

from contexts.knowledge.interfaces.config_access import has_config_read_access
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime import (
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)


def make_config_scoped_ws_router(
    *,
    path: str,
    get_config: Callable[[KnowledgeFacade, uuid.UUID], Awaitable[Any]],
    channel_fn: Callable[[uuid.UUID], str],
) -> APIRouter:
    """Build a one-route ``APIRouter`` for a project-scoped, config-gated WS channel.

    ``get_config`` resolves the config via a ``KnowledgeFacade`` bound to a
    request-scoped session (e.g. ``lambda f, cid: f.get_rag_config(cid)``);
    the returned object must expose ``project_id``. Flow: subprotocol auth
    (close 4401 on failure) -> unless admin, look up the config (close 4404 if
    missing) and apply the R11.17 owner-aware read ACL via
    ``has_config_read_access`` (close 4403 on denial: chatroom-owned Concept
    Maps go through the owning room's ACL, agent_group/workspace maps require
    project membership + ``concept_map_enabled``, RAG/Knowledge-Map configs
    require project membership) -> subscribe to ``channel_fn(config_id)`` via
    ``connection_loop``.
    """
    router = APIRouter(tags=["ws"])

    @router.websocket(path)
    async def _route(ws: WebSocket, config_id: uuid.UUID) -> None:
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
                    cfg = await get_config(facade, config_id)
                    if cfg is None:
                        await ws.close(code=4404)
                        return
                    # R11.17 owner-aware ACL: chatroom-owned Concept Maps go
                    # through the room ACL; agent_group/workspace maps require
                    # project membership + concept_map_enabled; RAG/Knowledge-Map
                    # configs (no owner_kind) require project membership.
                    if not await has_config_read_access(
                        session, principal=auth.principal, cfg=cfg
                    ):
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
            channels=[channel_fn(config_id)],
            token_expires_at=auth.expires_at,
            token_jti=auth.jti,
        )

    return router


__all__ = ["make_config_scoped_ws_router"]
