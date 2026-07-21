"""Gemini probe — `GET /v1/models` (R7.02).

Google AI Studio keys also authenticate by query string (`?key=…`), but we send
the secret in the `x-goog-api-key` header instead so it never lands in any URL —
httpx/httpcore's own loggers, upstream access logs, or proxies — none of which
this module controls.
"""

from __future__ import annotations

import httpx

from contexts.keys.infrastructure.probes.base import (
    ProbeResult,
    new_http_client,
    probe_url,
    summarise_http_failure,
)

_PATH = "/v1/models"


async def probe_gemini(secret: str) -> ProbeResult:
    try:
        async with new_http_client() as client:
            resp = await client.get(
                probe_url("gemini_base_url", _PATH),
                headers={"x-goog-api-key": secret},
            )
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code in (401, 403):
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


__all__ = ["probe_gemini"]
