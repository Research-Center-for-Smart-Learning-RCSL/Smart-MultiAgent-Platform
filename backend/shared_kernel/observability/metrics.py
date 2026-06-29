"""Prometheus metrics registry + HTTP-level middleware.

Seeds the three counters called out by B.10:
  * `http_requests_total{method,route,status}`
  * `db_pool_in_use`
  * `redis_command_errors_total`

Exposed via `/metrics` in a separate router (see `app.api.v1.metrics`).
Nginx is responsible for restricting the endpoint to localhost + the Nginx
upstream network (see `deploy/compose/nginx/conf.d/smap.conf`); this code
does not try to enforce that at the app layer.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

from app.config.settings import ObservabilitySection

REGISTRY: CollectorRegistry = CollectorRegistry()

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the backend.",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

DB_POOL_IN_USE = Gauge(
    "db_pool_in_use",
    "Number of DB connections currently checked out of the SQLAlchemy pool.",
    registry=REGISTRY,
)

DB_POOL_SIZE = Gauge(
    "db_pool_size",
    "Configured maximum size of the SQLAlchemy pool (pool_size + max_overflow).",
    registry=REGISTRY,
)

DB_POOL_AVAILABLE = Gauge(
    "db_pool_available",
    "Slots remaining before the SQLAlchemy pool is exhausted "
    "(db_pool_size - db_pool_in_use). Alert when this hits zero.",
    registry=REGISTRY,
)

REDIS_COMMAND_ERRORS = Counter(
    "redis_command_errors_total",
    "Redis commands that raised — bucketed by command name.",
    labelnames=("command",),
    registry=REGISTRY,
)

# ---- Phase F: Chat & Real-time metrics (F-chat-realtime.md §cross-cutting) --

WS_CONNECTIONS_ACTIVE = Gauge(
    "ws_connections_active",
    "Number of WebSocket connections currently open across all endpoints.",
    registry=REGISTRY,
)

WS_PER_USER_REJECTIONS = Counter(
    "ws_per_user_rejections_total",
    "WebSocket connections rejected because the per-user concurrent cap was hit.",
    registry=REGISTRY,
)

TUS_UPLOAD_BYTES = Counter(
    "tus_upload_bytes_total",
    "Total bytes received via the TUS resumable upload endpoint.",
    registry=REGISTRY,
)

MESSAGE_SANITIZE_REJECTIONS = Counter(
    "message_sanitize_rejections_total",
    "Server-side bleach passes that stripped at least one tag or attribute.",
    registry=REGISTRY,
)

EXPORT_JOBS = Counter(
    "export_jobs_total",
    "Chat export jobs enqueued.",
    registry=REGISTRY,
)

# ---- Phase D: API Key Management metrics (D.7 / D.8 / D.1 cross-cutting) ---

PROVIDER_CALL_TOTAL = Counter(
    "provider_calls_total",
    "Outbound provider calls dispatched by the key router.",
    labelnames=("provider", "status"),
    registry=REGISTRY,
)

KEY_GROUP_EXHAUSTED_TOTAL = Counter(
    "key_group_exhausted_total",
    "Key group exhaustion events raised by the router.",
    labelnames=("reason",),
    registry=REGISTRY,
)

ENVELOPE_DECRYPT_FAILURES_TOTAL = Counter(
    "envelope_decrypt_failures_total",
    "Envelope HMAC or AES-GCM decrypt failures (tamper or key mismatch).",
    registry=REGISTRY,
)

USAGE_THRESHOLD_EVENTS_TOTAL = Counter(
    "key_usage_threshold_events_total",
    "80% hourly-limit threshold events emitted by the sampler worker.",
    registry=REGISTRY,
)

# ---- Phase I: Workflow execution metrics (W22) -------------------------------

WORKFLOW_RUNS_TOTAL = Counter(
    "workflow_runs_total",
    "Workflow runs that reached a terminal state, labelled by final state.",
    labelnames=("state",),
    registry=REGISTRY,
)

WORKFLOW_STEP_DURATION_SECONDS = Histogram(
    "workflow_step_duration_seconds",
    "Wall-clock time from step creation to terminal state, labelled by node type.",
    labelnames=("node_type",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=REGISTRY,
)

WORKFLOW_STEPS_TOTAL = Counter(
    "workflow_steps_total",
    "Workflow steps that reached a terminal state, labelled by node type and state.",
    labelnames=("node_type", "state"),
    registry=REGISTRY,
)

# ---- Phase I.4: Retention worker metrics -----------------------------------

RETENTION_LAST_RUN_TIMESTAMP = Gauge(
    "retention_last_run_timestamp_seconds",
    "Unix timestamp of the most recent successful run for each retention worker.",
    labelnames=("worker",),
    registry=REGISTRY,
)

RETENTION_LAST_ROWS = Gauge(
    "retention_last_rows",
    "Rows affected by the most recent run of each retention worker.",
    labelnames=("worker",),
    registry=REGISTRY,
)

RETENTION_FAILURES = Counter(
    "retention_failures_total",
    "Retention worker exceptions, by worker name.",
    labelnames=("worker",),
    registry=REGISTRY,
)

# ---- Phase E.7/E.8: GraphRAG build state -----------------------------------

GRAPHRAG_BUILD_STATE = Gauge(
    "graphrag_build_state",
    "Current build state per GraphRAG config (1 = in this state). "
    "Labels: config_id, state ∈ {idle, building, ready, failed}.",
    labelnames=("config_id", "state"),
    registry=REGISTRY,
)

# ---- Phase I.6: Admin impersonation -----------------------------------------

ADMIN_IMPERSONATION_SESSIONS_ACTIVE = Gauge(
    "admin_impersonation_sessions_active",
    "Currently active admin impersonation sessions (started_at set, ended_at null).",
    registry=REGISTRY,
)

# ---- Trusted-proxy boundary health (SEC-M1) ---------------------------------

TRUSTED_PROXY_UNTRUSTED_PEER = Counter(
    "smap_trusted_proxy_untrusted_peer_total",
    "Requests carrying X-Forwarded-For whose immediate peer is NOT in "
    "SMAP_SEC_TRUSTED_PROXIES. The header is discarded and the client collapses "
    "to the peer IP — a non-zero rate means per-IP bans/limits and audit actor_ip "
    "are all keying on a single proxy address.",
    registry=REGISTRY,
)


class _MetricsMiddleware(BaseHTTPMiddleware):
    """Records `http_requests_total` with the route template, not the raw URL.

    Using the route template (from Starlette's router) keeps the cardinality
    bounded; the raw path would create a new time series for every UUID.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.monotonic()
        response: Response | None = None
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed = time.monotonic() - started
            route = request.scope.get("route")
            route_tpl = getattr(route, "path", None) or "__unmatched__"
            HTTP_REQUESTS.labels(
                method=request.method,
                route=route_tpl,
                status=str(status),
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=request.method,
                route=route_tpl,
            ).observe(elapsed)


def mount_metrics_middleware(app: FastAPI, cfg: ObservabilitySection) -> None:
    if cfg.metrics_enabled:
        app.add_middleware(_MetricsMiddleware)


__all__ = [
    "ADMIN_IMPERSONATION_SESSIONS_ACTIVE",
    "DB_POOL_AVAILABLE",
    "DB_POOL_IN_USE",
    "DB_POOL_SIZE",
    "ENVELOPE_DECRYPT_FAILURES_TOTAL",
    "EXPORT_JOBS",
    "GRAPHRAG_BUILD_STATE",
    "HTTP_REQUEST_DURATION",
    "HTTP_REQUESTS",
    "KEY_GROUP_EXHAUSTED_TOTAL",
    "MESSAGE_SANITIZE_REJECTIONS",
    "PROVIDER_CALL_TOTAL",
    "REDIS_COMMAND_ERRORS",
    "REGISTRY",
    "RETENTION_FAILURES",
    "RETENTION_LAST_ROWS",
    "RETENTION_LAST_RUN_TIMESTAMP",
    "TRUSTED_PROXY_UNTRUSTED_PEER",
    "TUS_UPLOAD_BYTES",
    "USAGE_THRESHOLD_EVENTS_TOTAL",
    "WORKFLOW_RUNS_TOTAL",
    "WORKFLOW_STEP_DURATION_SECONDS",
    "WORKFLOW_STEPS_TOTAL",
    "WS_CONNECTIONS_ACTIVE",
    "WS_PER_USER_REJECTIONS",
    "mount_metrics_middleware",
]
