"""GraphRAG build-progress WebSocket events (R11.04 live status).

The builder (and the reconciliation loop) publish a ``build.state`` event into
``ws:graphrag:{config_id}`` on every state transition so the frontend can show
live build progress instead of REST polling. Mirrors the RAG ingest path's
``ws:rag:{config_id}`` events.

Publishing is strictly best-effort: a Redis/pubsub outage must never fail (or
roll back) a build or a reconcile, so every error is swallowed here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from contexts.knowledge.infrastructure.channels import graphrag_channel
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)


async def publish_build_state(
    config_id: uuid.UUID,
    state: str,
    *,
    build_id: uuid.UUID | None = None,
    **extra: Any,
) -> None:
    """Best-effort publish of a GraphRAG build-state transition.

    Deliberately carries no error detail: the event broadcasts to every project
    member on ws:graphrag:{id}, and a raw provider/exception string could leak
    internal detail. The terminal ``state`` (failed / failed_compensating) is
    enough for the UI; the message lives in the audit row and REST
    ``last_build_error`` only.
    """
    payload: dict[str, Any] = {"state": state}
    if build_id is not None:
        payload["build_id"] = str(build_id)
    payload.update(extra)
    try:
        await Publisher(graphrag_channel(config_id)).emit("build.state", payload)
    except Exception:  # never let a telemetry hiccup affect the build
        _log.debug("graphrag build-state publish failed for %s", config_id, exc_info=True)


__all__ = ["publish_build_state"]
