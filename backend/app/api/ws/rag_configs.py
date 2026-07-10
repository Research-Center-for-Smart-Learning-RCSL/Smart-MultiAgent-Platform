"""`/ws/rag-configs/{id}` — document ingest status + per-doc progress.

Knowledge context publishes into `ws:rag:{config_id}`; ACL is "member of
the RAG config's project". Built on the shared scaffold in
:mod:`contexts.knowledge.interfaces.ws_config_route`, also used by
:mod:`app.api.ws.graphrag` and :mod:`app.api.ws.knowmap`.
"""

from __future__ import annotations

from contexts.knowledge.interfaces import rag_channel
from contexts.knowledge.interfaces.ws_config_route import make_config_scoped_ws_router

router = make_config_scoped_ws_router(
    path="/ws/rag-configs/{config_id}",
    get_config=lambda facade, config_id: facade.get_rag_config(config_id),
    channel_fn=rag_channel,
)

__all__ = ["router"]
