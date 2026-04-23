"""Gemini probe — `GET /v1/models` (R7.02).

Google AI Studio keys authenticate by query string (`?key=…`). No Bearer
scheme. The secret never lands in URL logs because the probe module never
logs full URLs.
"""

from __future__ import annotations

import httpx

from contexts.keys.infrastructure.probes.base import (
    ProbeResult,
    new_http_client,
    summarise_http_failure,
)

_URL = "https://generativelanguage.googleapis.com/v1/models"


async def probe_gemini(secret: str) -> ProbeResult:
    try:
        async with new_http_client() as client:
            resp = await client.get(_URL, params={"key": secret})
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code in (401, 403):
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


__all__ = ["probe_gemini"]
