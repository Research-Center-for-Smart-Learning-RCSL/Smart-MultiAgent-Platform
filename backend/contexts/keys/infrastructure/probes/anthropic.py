"""Anthropic (`claude`) probe — `POST /v1/messages` with a 1-token stub (R7.02)."""

from __future__ import annotations

import httpx

from contexts.keys.infrastructure.probes.base import (
    ProbeResult,
    new_http_client,
    summarise_http_failure,
)

_URL = "https://api.anthropic.com/v1/messages"
_VERSION_HEADER = "2023-06-01"  # stable minimum that supports 1-token requests


async def probe_anthropic(secret: str) -> ProbeResult:
    headers = {
        "x-api-key": secret,
        "anthropic-version": _VERSION_HEADER,
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-3-5-haiku-latest",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        async with new_http_client() as client:
            resp = await client.post(_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code == 401:
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


__all__ = ["probe_anthropic"]
