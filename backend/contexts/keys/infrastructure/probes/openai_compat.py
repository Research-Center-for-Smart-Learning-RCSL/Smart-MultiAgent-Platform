"""OpenAI-compatible probe -- ``GET {base_url}/models`` (R7.02, R7.16).

Unlike the built-in OpenAI probe, the base URL is user-supplied (per-key
``config.base_url``), not read from ``settings.provider_probe``. The URL
is validated against SSRF rules by :func:`validate_base_url` before any
network call.
"""

from __future__ import annotations

import httpx

from contexts.keys.infrastructure.probes.base import (
    ProbeResult,
    new_http_client,
    summarise_http_failure,
    validate_base_url,
)


async def probe_openai_compat(secret: str, *, base_url: str) -> ProbeResult:
    try:
        validated_url = validate_base_url(base_url)
    except ValueError as exc:
        return ProbeResult.failed(str(exc))

    url = f"{validated_url}/v1/models"
    headers = {"Authorization": f"Bearer {secret}"}
    try:
        async with new_http_client() as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return ProbeResult.failed(f"network: {exc.__class__.__name__}")
    if resp.status_code == 200:
        return ProbeResult.ok()
    if resp.status_code == 401:
        return ProbeResult.failed("unauthorized")
    return ProbeResult.failed(summarise_http_failure(resp))


__all__ = ["probe_openai_compat"]
