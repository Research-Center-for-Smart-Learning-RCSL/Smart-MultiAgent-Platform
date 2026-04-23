"""Qdrant readiness — HTTP `GET /readyz` (single round-trip)."""

from __future__ import annotations

import httpx

from app.config.settings import Settings

from .base import ProbeResult


async def probe_qdrant(settings: Settings) -> ProbeResult:
    url = settings.qdrant.url.rstrip("/") + "/readyz"
    headers: dict[str, str] = {}
    if settings.qdrant.api_key:
        headers["api-key"] = settings.qdrant.api_key
    async with httpx.AsyncClient(timeout=1.5) as http:
        resp = await http.get(url, headers=headers)
    if resp.status_code != 200:
        return ProbeResult("qdrant", False, f"status={resp.status_code}")
    return ProbeResult("qdrant", True)
