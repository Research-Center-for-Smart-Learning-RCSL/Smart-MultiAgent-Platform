"""Prometheus metrics for the workflow context (H)."""

from __future__ import annotations

from prometheus_client import Counter

from shared_kernel.observability.metrics import REGISTRY

# F-4 (R14.07a): event-triggered runs refused by the per-workflow rolling-window
# budget. A sustained nonzero rate is the signal that a workflow is looping or
# mis-authored — the breaker holding is exactly what this counts.
WORKFLOW_TRIGGER_THROTTLED = Counter(
    "workflow_trigger_throttled_total",
    "Event-triggered workflow runs refused by the per-workflow trigger budget.",
    registry=REGISTRY,
)


__all__ = ["WORKFLOW_TRIGGER_THROTTLED"]
