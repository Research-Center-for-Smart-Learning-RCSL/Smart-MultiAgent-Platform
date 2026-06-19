"""Vault readiness — `GET /v1/sys/health?standbyok=true`."""

from __future__ import annotations

import httpx

from app.config.settings import Settings

from .base import ProbeResult


async def probe_vault(settings: Settings) -> ProbeResult:
    url = settings.vault.addr.rstrip("/") + "/v1/sys/health?standbyok=true"
    verify = settings.vault.ca_cert or True
    async with httpx.AsyncClient(timeout=1.5, verify=verify) as http:
        resp = await http.get(url)
    # Vault returns 200 for active, 429 for standby (both healthy with
    # standbyok=true). 503 = sealed/unhealthy.
    if resp.status_code in (200, 429):
        return ProbeResult("vault", True)
    return ProbeResult("vault", False, f"status={resp.status_code}")
