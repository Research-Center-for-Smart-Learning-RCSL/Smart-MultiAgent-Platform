"""Prometheus metrics for orchestration (G.9).

Covers A2A transport (G.1), wake-up (G.3), approval (G.6),
instruct (G.7), and sub-agent (G.8) metrics.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from shared_kernel.observability.metrics import REGISTRY

# -- A2A (G.1) ---------------------------------------------------------------

A2A_MESSAGES = Counter(
    "a2a_messages_total",
    "Total A2A messages sent between agents.",
    labelnames=("type",),
    registry=REGISTRY,
)

A2A_DLQ = Counter(
    "a2a_dlq_total",
    "A2A messages moved to DLQ after exhausting retries.",
    registry=REGISTRY,
)

# -- Wake-up (G.3) -----------------------------------------------------------

WAKEUP_FIRES = Counter(
    "wakeup_fires_total",
    "Wake-up triggers fired.",
    labelnames=("kind",),
    registry=REGISTRY,
)

# -- Approval (G.6) ----------------------------------------------------------

APPROVAL_RESOLUTIONS = Counter(
    "approval_resolutions_total",
    "Approval gate resolutions by mode and outcome.",
    labelnames=("mode", "outcome"),
    registry=REGISTRY,
)

# -- Instruct (G.7) ----------------------------------------------------------

INSTRUCT_CHAIN_DEPTH = Histogram(
    "instruct_chain_depth",
    "Depth of instruct chains when issued.",
    buckets=(1, 2, 3, 4, 5, 10, 20),
    registry=REGISTRY,
)

# -- Sub-agent (G.8) ---------------------------------------------------------

SUBAGENT_CONCURRENCY = Gauge(
    "subagent_concurrency",
    "Currently alive sub-agents per parent agent.",
    labelnames=("parent",),
    registry=REGISTRY,
)


__all__ = [
    "A2A_DLQ",
    "A2A_MESSAGES",
    "APPROVAL_RESOLUTIONS",
    "INSTRUCT_CHAIN_DEPTH",
    "SUBAGENT_CONCURRENCY",
    "WAKEUP_FIRES",
]
