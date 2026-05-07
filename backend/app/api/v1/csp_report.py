"""CSP violation report ingress (R19a.06).

Browsers POST the report as `application/csp-report` (newer browsers also
send `application/reports+json`). We accept both opaque shapes and log at
warning level — aggregation/alerting is Phase I's responsibility.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from loguru import logger

router = APIRouter(prefix="/api", tags=["csp"])


@router.post("/csp-report", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def csp_report(request: Request) -> None:
    # Do not enforce JSON schema — browsers send slightly different shapes.
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = (await request.body()).decode("utf-8", errors="replace")
    logger.bind(
        event="csp_violation",
        user_agent=request.headers.get("User-Agent"),
    ).warning(body if isinstance(body, str) else repr(body))


__all__ = ["router"]
