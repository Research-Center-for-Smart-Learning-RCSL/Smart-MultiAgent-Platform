"""Dependency probe adapters (B.5).

Each probe is a pure async callable that takes `Settings` and returns a
`ProbeResult`. The composite `probe_all` fans out under a 2-second budget
per operations.md §2.2.

SoC: the `app.api.v1.readyz` endpoint composes these results into problem+json.
Probes never import FastAPI or emit HTTP themselves. The bootstrap CLI reuses
the same probes for `qdrant-init` / readiness checks — same code, two callers.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.config.settings import Settings

from .base import ProbeResult
from .minio import probe_minio
from .neo4j import probe_neo4j
from .postgres import probe_postgres
from .qdrant import probe_qdrant
from .redis import probe_redis
from .vault import probe_vault

Probe = Callable[[Settings], Awaitable[ProbeResult]]

_PROBES: tuple[tuple[str, Probe], ...] = (
    ("postgres", probe_postgres),
    ("redis", probe_redis),
    ("qdrant", probe_qdrant),
    ("neo4j", probe_neo4j),
    ("minio", probe_minio),
    ("vault", probe_vault),
)

_BUDGET_SECONDS = 2.0


async def probe_all(settings: Settings) -> list[ProbeResult]:
    """Fan out every probe with a shared time budget; never let one hang."""

    async def _bounded(name: str, fn: Probe) -> ProbeResult:
        try:
            return await asyncio.wait_for(fn(settings), timeout=_BUDGET_SECONDS)
        except TimeoutError:
            return ProbeResult(name=name, ok=False, detail="timeout")
        except Exception as exc:  # — probes MUST NOT propagate
            return ProbeResult(name=name, ok=False, detail=f"{type(exc).__name__}: {exc}")

    return list(await asyncio.gather(*(_bounded(name, fn) for name, fn in _PROBES)))


__all__ = ["ProbeResult", "probe_all"]
