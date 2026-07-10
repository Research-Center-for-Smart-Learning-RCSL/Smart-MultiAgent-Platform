"""`/ws/graphrag/{id}` — live GraphRAG build state.

The knowledge context publishes ``build.state`` events into
``ws:graphrag:{config_id}`` as the 2PC builder (and the reconciler) move
through their states; ACL is "member of the GraphRAG config's project".
Built on the shared scaffold in
:mod:`contexts.knowledge.interfaces.ws_config_route`, also used by
:mod:`app.api.ws.rag_configs` and :mod:`app.api.ws.knowmap`.
"""

from __future__ import annotations

from contexts.knowledge.interfaces import graphrag_channel
from contexts.knowledge.interfaces.ws_config_route import make_config_scoped_ws_router

router = make_config_scoped_ws_router(
    path="/ws/graphrag/{config_id}",
    get_config=lambda facade, config_id: facade.get_graphrag_config(config_id),
    channel_fn=graphrag_channel,
)

__all__ = ["router"]
