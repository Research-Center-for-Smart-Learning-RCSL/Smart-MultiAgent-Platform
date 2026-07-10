"""GraphRAG / Knowledge Map build-progress WebSocket events (R11.04 live status).

The builder (and the reconciliation loop) publish a ``build.state`` event on
every state transition so the frontend can show live build progress instead
of REST polling — into ``ws:graphrag:{config_id}`` for a Concept Map build,
``ws:knowmap:{config_id}`` for a Knowledge Map build (Phase 3β, R11.24). The
caller names the channel explicitly; this module has no default. Mirrors the
RAG ingest path's ``ws:rag:{config_id}`` events.

Publishing is strictly best-effort: a Redis/pubsub outage must never fail (or
roll back) a build or a reconcile, so every error is swallowed here.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)


async def publish_build_state(
    config_id: uuid.UUID,
    state: str,
    *,
    channel: str,
    build_id: uuid.UUID | None = None,
    **extra: Any,
) -> None:
    """Best-effort publish of a GraphRAG (or Knowledge Map) build-state transition.

    Deliberately carries no error detail: the event broadcasts to every project
    member on the target channel, and a raw provider/exception string could
    leak internal detail. The terminal ``state`` (failed / failed_compensating)
    is enough for the UI; the message lives in the audit row and REST
    ``last_build_error`` only.

    ``channel`` is required, not defaulted: the shared builder/reconciler
    engine (R11.15) drives more than one product (Concept Map, Knowledge Map)
    over the same config-id-agnostic Cypher, so it has no way to infer which
    domain's channel a call belongs to. A silent default here previously let a
    misconfigured caller's build-state events land on the wrong product's
    channel with no error (found in code review) — every caller must now name
    its channel explicitly (``graphrag_channel(config_id)`` /
    ``knowmap_channel(config_id)``).
    """
    payload: dict[str, Any] = {"state": state}
    if build_id is not None:
        payload["build_id"] = str(build_id)
    payload.update(extra)
    try:
        await Publisher(channel).emit("build.state", payload)
    except Exception:  # never let a telemetry hiccup affect the build
        _log.debug("build-state publish failed for %s on %s", config_id, channel, exc_info=True)


__all__ = ["publish_build_state"]
