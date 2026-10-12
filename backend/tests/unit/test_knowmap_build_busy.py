"""A Knowledge Map build that finds the config busy waits for its turn.

``docs/tasks/2026-10-12-graph-build-busy-retry``: a busy ``knowmap_build`` used to
fail, and arq kept that failed result under the revision-keyed job id for
``keep_result``, so the follow-up build for the same revision was dropped as a
duplicate. A busy job now raises ``arq.Retry`` and stays queued under its id.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from arq.worker import Retry

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


def _builder_raising(exc: Exception) -> Any:
    class _Builder:
        def __init__(self, **_k: Any) -> None:
            pass

        async def run(self, **_k: Any) -> Any:
            raise exc

    return _Builder


async def _run_knowmap_build(exc: Exception, finalize: AsyncMock | None = None) -> None:
    from app.workers.tasks import knowmap as kmod

    repo = AsyncMock()
    repo.get.return_value = SimpleNamespace(id=uuid.uuid4())
    settings = SimpleNamespace(
        neo4j=SimpleNamespace(url="bolt://x", user="u", password="p"),
        qdrant=SimpleNamespace(url="http://x", api_key=""),
    )
    with (
        patch("app.config.settings.get_settings", return_value=settings),
        patch("contexts.knowledge.infrastructure.neo4j_driver.Neo4jAsyncDriver", _Closable),
        patch("qdrant_client.AsyncQdrantClient", _Closable),
        patch.object(kmod, "get_sessionmaker", return_value=_Session),
        patch.object(kmod, "KnowmapConfigRepository", return_value=repo),
        patch.object(kmod, "build_router", return_value=object()),
        patch.object(kmod, "_make_knowmap_embedder_factory", return_value=object()),
        patch.object(kmod, "GraphRagBuilder", _builder_raising(exc)),
        patch.object(kmod, "_finalize_build_revision", finalize or AsyncMock()),
    ):
        await kmod.knowmap_build({}, config_id=str(uuid.uuid4()), target_revision=2)


@pytest.mark.asyncio
async def test_busy_build_is_retried_after_the_configured_defer() -> None:
    with pytest.raises(Retry) as caught:
        await _run_knowmap_build(GraphRagBuildBusy("held"))

    from contexts.knowledge.application.graphrag_builder import BUILD_BUSY_RETRY_DEFER_S

    assert caught.value.defer_score == BUILD_BUSY_RETRY_DEFER_S * 1000


@pytest.mark.asyncio
async def test_busy_build_does_not_finalize_a_revision() -> None:
    finalize = AsyncMock()
    with pytest.raises(Retry):
        await _run_knowmap_build(GraphRagBuildBusy("held"), finalize)
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_build_errors_still_fail_the_job() -> None:
    boom = RuntimeError("neo4j down")
    with pytest.raises(RuntimeError) as caught:
        await _run_knowmap_build(boom)
    assert caught.value is boom
