"""`/metrics` endpoint (Prometheus exposition format).

Nginx restricts this endpoint to localhost + upstream (see
`deploy/compose/nginx/conf.d/smap.conf`). The app-level router only decides
whether the endpoint is mounted at all, via `settings.observability.metrics_enabled`.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from shared_kernel.observability.metrics import REGISTRY

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )
