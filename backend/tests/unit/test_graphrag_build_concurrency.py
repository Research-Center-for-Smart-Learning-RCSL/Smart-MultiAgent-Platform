"""Unit tests for bounded GraphRAG build concurrency (Phase 2a D8, AC-11).

Builds are heavy (LLM extraction + Neo4j + Qdrant), so a burst must not
monopolise the shared worker. ``graphrag_build`` gates its work behind a
per-worker semaphore whose cap comes from settings.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import app.workers.tasks.graphrag as graphrag


@pytest.mark.asyncio
async def test_graphrag_build_concurrency_is_bounded(monkeypatch: Any) -> None:
    monkeypatch.setattr(graphrag, "_graphrag_build_concurrency", lambda: 2)
    graphrag._reset_build_semaphore()

    active = 0
    peak = 0

    async def fake_run(*, config_id: str, triggered_by: str = "manual") -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    monkeypatch.setattr(graphrag, "_run_build", fake_run)

    try:
        results = await asyncio.gather(
            *[graphrag.graphrag_build({}, config_id=str(uuid.uuid4())) for _ in range(8)]
        )
        # All builds still complete — the semaphore serialises, never drops them.
        assert results == ["ok"] * 8
        # ...but no more than the configured cap run their heavy work at once.
        assert peak <= 2
    finally:
        graphrag._reset_build_semaphore()


def test_build_concurrency_reads_settings_and_floors_at_one(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        graphrag,
        "get_settings",
        lambda: SimpleNamespace(graphrag=SimpleNamespace(build_concurrency=7)),
    )
    assert graphrag._graphrag_build_concurrency() == 7

    # A non-positive cap floors at 1 so a build can always make progress.
    monkeypatch.setattr(
        graphrag,
        "get_settings",
        lambda: SimpleNamespace(graphrag=SimpleNamespace(build_concurrency=0)),
    )
    assert graphrag._graphrag_build_concurrency() == 1
