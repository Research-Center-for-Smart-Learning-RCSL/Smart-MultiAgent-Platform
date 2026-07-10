"""`/ws/knowmap/{id}` — live Knowledge Map build state.

The knowledge context publishes ``build.state`` events into
``ws:knowmap:{config_id}`` as the 2PC builder (and the reconciler) move
through their states; ACL is "member of the Knowledge Map config's project"
(Phase 3β, R11.24). Built on the shared scaffold in
:mod:`contexts.knowledge.interfaces.ws_config_route`, also used by
:mod:`app.api.ws.graphrag` and :mod:`app.api.ws.rag_configs`.
"""

from __future__ import annotations

from contexts.knowledge.interfaces import knowmap_channel
from contexts.knowledge.interfaces.ws_config_route import make_config_scoped_ws_router

router = make_config_scoped_ws_router(
    path="/ws/knowmap/{config_id}",
    get_config=lambda facade, config_id: facade.get_knowmap_config(config_id),
    channel_fn=knowmap_channel,
)

__all__ = ["router"]
