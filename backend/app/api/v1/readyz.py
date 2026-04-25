"""Readiness probe (B.5 / R3.03).

Green only if every Phase-B dependency — Postgres, Redis, Qdrant, Neo4j,
MinIO, Vault — answers within a 2-second fan-out budget. Failure returns
RFC 7807 `application/problem+json` with `type=https://smap.local/problems/dependency-unavailable`
and a `dependencies` field naming every failed probe.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from loguru import logger

from app.config.settings import get_settings
from shared_kernel.errors.problem import Problem
from shared_kernel.errors.registry import DEPENDENCY_UNAVAILABLE
from shared_kernel.infra.probes import ProbeResult, probe_all

router = APIRouter(tags=["health"])

_READYZ_CACHE_SECONDS = 2.0
_cached: tuple[float, list[ProbeResult]] | None = None


async def _load_results() -> list[ProbeResult]:
    """Cache readyz results for 2 s per process (operations.md §2.2 O2.02).

    Prevents stampede when Nginx or a probing load balancer hits every backend
    replica in parallel every second.
    """
    global _cached
    now = time.monotonic()
    if _cached is not None and now - _cached[0] < _READYZ_CACHE_SECONDS:
        return _cached[1]
    results = await probe_all(get_settings())
    _cached = (now, results)
    return results


@router.get("/readyz")
async def readyz() -> Response:
    results = await _load_results()
    failed = [r for r in results if not r.ok]
    if not failed:
        return JSONResponse(
            status_code=200,
            content={
                "status": "ok",
                "dependencies": {r.name: "ok" for r in results},
            },
        )
    # Operators chasing a 503 page need the probe-by-probe breakdown in their
    # logs without having to curl /readyz themselves — every failed dep is
    # logged with its detail string at WARNING.
    logger.bind(
        event="readyz_dependency_unavailable",
        failed=[r.name for r in failed],
        dependencies={
            r.name: ("ok" if r.ok else (r.detail or "unavailable"))
            for r in results
        },
    ).warning(
        f"/readyz returning 503 — failed: {', '.join(f.name for f in failed)}"
    )
    problem = Problem(
        type=DEPENDENCY_UNAVAILABLE,
        title="One or more upstream dependencies are unavailable.",
        status=503,
        detail="Failed: " + ", ".join(f.name for f in failed),
        extras={
            "dependencies": {
                r.name: ("ok" if r.ok else f"down: {r.detail or 'unavailable'}")
                for r in results
            },
        },
    )
    return JSONResponse(
        status_code=503,
        content=problem.dump(),
        media_type="application/problem+json",
    )
