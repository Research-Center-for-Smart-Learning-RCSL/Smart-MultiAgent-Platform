"""A Concept Map build that finds the config busy waits for its turn.

``docs/tasks/2026-10-12-graph-build-busy-retry`` (Q-3): a busy ``graphrag_build``
used to be recorded as a failed job and flip the build-state gauge to ``failed``
while the config's real build was still running. It now raises ``arq.Retry`` and
leaves the gauge to the build that holds the lock. Also pins that both build
functions are registered with a retry budget that outlasts the build timeout.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from arq.worker import Function, Retry

import app.workers.tasks.graphrag as graphrag
from contexts.knowledge.domain.errors import GraphRagBuildBusy


class _Session:
    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


class _Closable:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def close(self) -> None:
        return None


class _Gauge:
    def __init__(self) -> None:
        self.values: dict[str, float] = {}

    def labels(self, *, config_id: str, state: str) -> Any:
        values = self.values

        class _Child:
            def set(self, v: float) -> None:
                values[state] = v

        return _Child()


async def _run_graphrag_build(exc: Exception, gauge: _Gauge | None = None) -> None:
    class _Builder:
        def __init__(self, **_k: Any) -> None:
            pass

        async def run(self, **_k: Any) -> Any:
            raise exc

    repo = AsyncMock()
    repo.get.return_value = SimpleNamespace(id=uuid.uuid4())
    settings = SimpleNamespace(
        neo4j=SimpleNamespace(url="bolt://x", user="u", password="p"),
        qdrant=SimpleNamespace(url="http://x", api_key=""),
    )
    with (
        patch.object(graphrag, "get_settings", return_value=settings),
        patch.object(graphrag, "Neo4jAsyncDriver", _Closable),
        patch.object(graphrag, "AsyncQdrantClient", _Closable),
        patch.object(graphrag, "get_sessionmaker", return_value=_Session),
        patch.object(graphrag, "GraphRagConfigRepository", return_value=repo),
        patch.object(graphrag, "build_router", return_value=object()),
        patch.object(graphrag, "_resolve_delta_scope", AsyncMock(return_value=None)),
        patch.object(graphrag, "_make_embedder_factory", return_value=object()),
        patch.object(graphrag, "GraphRagBuilder", _Builder),
        patch.object(graphrag, "GRAPHRAG_BUILD_STATE", gauge or _Gauge()),
    ):
        await graphrag._run_build(config_id=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_busy_concept_map_build_is_retried_after_the_configured_defer() -> None:
    with pytest.raises(Retry) as caught:
        await _run_graphrag_build(GraphRagBuildBusy("held"))

    from contexts.knowledge.application.graphrag_builder import BUILD_BUSY_RETRY_DEFER_S

    assert caught.value.defer_score == BUILD_BUSY_RETRY_DEFER_S * 1000


@pytest.mark.asyncio
async def test_busy_concept_map_build_does_not_report_failed() -> None:
    gauge = _Gauge()
    with pytest.raises(Retry):
        await _run_graphrag_build(GraphRagBuildBusy("held"), gauge)
    assert gauge.values.get("failed", 0.0) == 0.0


@pytest.mark.asyncio
async def test_other_concept_map_build_errors_still_fail_and_report_failed() -> None:
    boom = RuntimeError("neo4j down")
    gauge = _Gauge()
    with pytest.raises(RuntimeError) as caught:
        await _run_graphrag_build(boom, gauge)
    assert caught.value is boom
    assert gauge.values["failed"] == 1.0


def _registered(name: str) -> Function:
    from app.workers.main import WorkerSettings

    for entry in WorkerSettings.functions:
        if isinstance(entry, Function) and entry.name == name:
            return entry
    raise AssertionError(f"{name} is not registered with arq.func()")


@pytest.mark.parametrize(
    ("name", "timeout_attr"),
    [
        ("graphrag_build", "app.workers.tasks.graphrag.GRAPHRAG_BUILD_TIMEOUT_S"),
        ("knowmap_build", "app.workers.tasks.knowmap.KNOWMAP_BUILD_TIMEOUT_S"),
    ],
)
def test_busy_retry_budget_outlasts_the_build_timeout(name: str, timeout_attr: str) -> None:
    import importlib

    from contexts.knowledge.application.graphrag_builder import BUILD_BUSY_RETRY_DEFER_S

    module_name, attr = timeout_attr.rsplit(".", 1)
    timeout = getattr(importlib.import_module(module_name), attr)
    fn = _registered(name)
    assert fn.timeout_s == timeout
    assert fn.max_tries is not None
    # arq runs try N only while N <= max_tries, so a job waits through
    # (max_tries - 1) defers before it gives up.
    assert (fn.max_tries - 1) * BUILD_BUSY_RETRY_DEFER_S >= timeout
