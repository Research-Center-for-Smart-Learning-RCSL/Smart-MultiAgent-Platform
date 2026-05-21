"""CSP violation report ingress (R19a.06).

Browsers POST the report as `application/csp-report` (newer browsers also
send `application/reports+json`). We accept both opaque shapes and log at
warning level — aggregation/alerting is Phase I's responsibility.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger

router = APIRouter(prefix="/api", tags=["csp"])

# This endpoint is unauthenticated and exempt from rate limiting (API-10), so
# the request body is the only attacker-controlled, unbounded input. Genuine
# CSP reports are a few hundred bytes; 16 KiB leaves headroom for batched
# `reports+json` payloads while stopping a single request from buffering an
# arbitrarily large body into memory.
_MAX_REPORT_BYTES = 16 * 1024
# The logged representation is truncated independently of the read cap so a
# report sitting just under the byte limit cannot dominate a log line.
_MAX_LOG_CHARS = 2048


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail="CSP report exceeds maximum accepted size",
    )


async def _read_capped_body(request: Request) -> bytes:
    """Read the request body, refusing anything over `_MAX_REPORT_BYTES`.

    `Content-Length` is checked first as a cheap early-out, but it is advisory
    (absent on chunked uploads, trivially spoofed), so the streamed chunks are
    counted as the authoritative limit.
    """
    declared = request.headers.get("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_REPORT_BYTES:
        raise _too_large()
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_REPORT_BYTES:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/csp-report", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def csp_report(request: Request) -> None:
    # Read the body ourselves with a hard cap — `request.json()` would buffer
    # the entire (unbounded) body before we ever see its size.
    raw = await _read_capped_body(request)
    text = raw.decode("utf-8", errors="replace")
    # Do not enforce a JSON schema — browsers send slightly different shapes;
    # fall back to the raw (already capped) text when it is not valid JSON.
    try:
        body = json.loads(text)
    except ValueError:
        body = text
    rendered = body if isinstance(body, str) else repr(body)
    if len(rendered) > _MAX_LOG_CHARS:
        rendered = rendered[:_MAX_LOG_CHARS] + "... [truncated]"
    logger.bind(
        event="csp_violation",
        user_agent=request.headers.get("User-Agent"),
    ).warning(rendered)


__all__ = ["router"]
