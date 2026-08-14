"""F-4 — the Knowledge Map half of the reconciliation cron.

The knowmap revision sweep deliberately leaves stuck RUNNING configs alone (Q-2)
because the reconciler already reclaims them. That guarantee rested entirely on two
uncovered lines in ``reconcile_once``: if the knowmap pass were ever dropped, nothing
would fail and knowmap builds would silently wedge in RUNNING forever, since both
``trigger_build`` and the builder refuse a config in that state.

These tests pin the wiring and the stuck-state set the sweep's design depends on.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

import app.workers.graphrag_reconciler as recon_mod
from contexts.knowledge.application.graphrag_reconciler import _STUCK_STATES
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository


class _FakeLoop:
    def __init__(self, healed: list[uuid.UUID]) -> None:
        self._healed = healed
        self.ran = 0

    async def run_once(self) -> list[uuid.UUID]:
        self.ran += 1
        return self._healed


@pytest.mark.asyncio
async def test_reconcile_once_runs_the_knowmap_pass_too(monkeypatch: Any) -> None:
    # Dropping the knowmap pass is silent: the graphrag pass still returns and the
    # cron still reports success, while knowmap configs wedge in RUNNING.
    graphrag_id, knowmap_id = uuid.uuid4(), uuid.uuid4()
    graphrag_loop = _FakeLoop([graphrag_id])
    knowmap_loop = _FakeLoop([knowmap_id])

    @asynccontextmanager
    async def _fake_graphrag() -> Any:
        yield graphrag_loop

    @asynccontextmanager
    async def _fake_knowmap() -> Any:
        yield knowmap_loop

    monkeypatch.setattr(recon_mod, "_loop", _fake_graphrag)
    monkeypatch.setattr(recon_mod, "_knowmap_loop", _fake_knowmap)

    healed = await recon_mod.reconcile_once()

    assert knowmap_loop.ran == 1
    assert graphrag_loop.ran == 1
    # Both passes' results reach the caller; the cron logs the combined count.
    assert healed == [graphrag_id, knowmap_id]


@pytest.mark.asyncio
async def test_knowmap_loop_is_wired_to_the_knowmap_repository(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Capture:
        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

    monkeypatch.setattr(recon_mod, "ReconciliationLoop", _Capture)
    monkeypatch.setattr(recon_mod, "Neo4jAsyncDriver", lambda **_kw: _Closeable())
    monkeypatch.setattr(recon_mod, "GraphRagVectorStore", lambda *_a, **_kw: object())
    monkeypatch.setattr(recon_mod, "RedisSnapshotStore", lambda *_a, **_kw: object())
    monkeypatch.setattr(recon_mod, "RedisBuildLockStore", lambda *_a, **_kw: object())
    monkeypatch.setattr(recon_mod, "get_sessionmaker", lambda: object)
    monkeypatch.setattr(recon_mod, "_make_phase2_retry", lambda *_a, **_kw: object())

    import qdrant_client

    monkeypatch.setattr(qdrant_client, "AsyncQdrantClient", lambda **_kw: _Closeable())

    async with recon_mod._knowmap_loop():
        pass

    assert captured["repo_factory"] is KnowmapConfigRepository
    assert captured["resource_type"] == "knowmap_config"
    # A lock store is mandatory: without one the reconciler skips RUNNING configs
    # outright, which is exactly the state the sweep is relying on it to clear.
    assert captured["lock_store"] is not None
    # Knowledge Map builds run with replace=True, so a recovered phase 2 must run
    # the build-scoped vector sweep the failed original never reached.
    assert captured["replace_on_recovery"] is True


class _Closeable:
    async def close(self) -> None:
        return None


def test_running_is_in_the_reconcilers_stuck_states() -> None:
    # The sweep's Q-2 decision (enqueue IDLE only, never RUNNING) is only safe
    # while the reconciler owns RUNNING recovery. If this set ever loses RUNNING,
    # nothing reclaims a hard-killed build and the sweep must be revisited.
    assert BuildState.RUNNING in _STUCK_STATES
