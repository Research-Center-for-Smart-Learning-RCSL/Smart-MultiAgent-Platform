"""Liveness probe — process-level only. `/readyz` (multi-dep) lands in Phase B."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
