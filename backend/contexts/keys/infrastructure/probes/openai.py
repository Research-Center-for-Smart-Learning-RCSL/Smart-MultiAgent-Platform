"""OpenAI probe — `GET /v1/models` (R7.02)."""

from __future__ import annotations

import httpx

from contexts.keys.infrastructure.probes.base import (
    ProbeResult,
    new_http_client,
    summarise_http_failure,
)

_URL = "https://api.openai.com/v1/models"


async def probe_openai(secret: str) -> ProbeResult:
    headers = {"authorization": f"Bearer {secret}"}
    try:
        async with new_http_client() as client:
            resp = await client.get(_URL, headers=headers)
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code == 401:
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


__all__ = ["probe_openai"]
