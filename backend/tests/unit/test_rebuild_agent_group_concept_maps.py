"""``rebuild-agent-group-concept-maps`` and the worker's full-rebuild switch (AC-4).

The listing SQL runs against real Postgres in
``tests/wiring/test_rebuild_agent_group_concept_maps.py``; the provenance a
rebuild then writes is checked live on the compose stack (dossier D-n).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from smap.maintenance import rebuild_agent_group_concept_maps as cmd


def _maps(n: int) -> tuple[cmd.GroupMap, ...]:
    return tuple(
        cmd.GroupMap(
            config_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            agent_group_id=uuid.uuid4(),
            members=2,
            messages=i,
        )
        for i in range(n)
    )


class _Pool:
    def __init__(self, *, dedupe: frozenset[str] = frozenset(), fail: frozenset[str] = frozenset()) -> None:
        self.calls: list[dict[str, Any]] = []
        self._dedupe, self._fail = dedupe, fail

    async def enqueue_job(self, name: str, **kwargs: Any) -> Any:
        self.calls.append({"name": name, **kwargs})
        if kwargs["config_id"] in self._fail:
            raise ConnectionError("redis down")
        return None if kwargs["config_id"] in self._dedupe else object()


@pytest.mark.asyncio
async def test_a_dry_run_lists_and_enqueues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cmd.ARMED_ENV, raising=False)
    maps = _maps(2)
    pool = _Pool()
    with patch.object(cmd, "list_group_maps", AsyncMock(return_value=maps)):
        report = await cmd.run(pool=pool)
    assert (report.dry_run, report.maps, report.enqueued) == (True, maps, 0)
    assert pool.calls == []


@pytest.mark.asyncio
async def test_an_armed_run_enqueues_one_full_rebuild_per_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cmd.ARMED_ENV, "1")
    maps = _maps(3)
    pool = _Pool(dedupe=frozenset({str(maps[1].config_id)}), fail=frozenset({str(maps[2].config_id)}))
    with patch.object(cmd, "list_group_maps", AsyncMock(return_value=maps)):
        report = await cmd.run(pool=pool)
    assert (report.enqueued, report.deduped, report.failed) == (1, 1, (maps[2].config_id,))
    assert all(c["name"] == "graphrag_build" and c["full_rebuild"] is True for c in pool.calls)
    # Its own job id, never the ordinary build's, so it cannot be deduplicated
    # against a regular build of the same map.
    job_ids = [c["_job_id"] for c in pool.calls]
    assert all(j.startswith(f"graphrag:rebuild:{gm.config_id}:") for j, gm in zip(job_ids, maps, strict=True))


@pytest.mark.asyncio
async def test_a_map_whose_group_has_no_live_member_is_never_enqueued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A replace build reads no message for it and would prune the whole map.
    monkeypatch.setenv(cmd.ARMED_ENV, "1")
    live, empty = _maps(2)
    empty = cmd.GroupMap(
        config_id=empty.config_id,
        project_id=empty.project_id,
        agent_group_id=empty.agent_group_id,
        members=0,
        messages=0,
    )
    pool = _Pool()
    with patch.object(cmd, "list_group_maps", AsyncMock(return_value=(live, empty))):
        report = await cmd.run(pool=pool)
    assert [c["config_id"] for c in pool.calls] == [str(live.config_id)]
    assert report.skipped_empty == (empty.config_id,)


def test_a_failed_enqueue_exits_non_zero() -> None:
    from smap.maintenance.__main__ import app

    report = cmd.RebuildReport(dry_run=False, maps=_maps(1), failed=(uuid.uuid4(),))
    with patch.object(cmd, "run", AsyncMock(return_value=report)):
        assert CliRunner().invoke(app, ["rebuild-agent-group-concept-maps"]).exit_code == 1
    ok = cmd.RebuildReport(dry_run=False, maps=_maps(1), enqueued=1)
    with patch.object(cmd, "run", AsyncMock(return_value=ok)):
        assert CliRunner().invoke(app, ["rebuild-agent-group-concept-maps"]).exit_code == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("full_rebuild", "expected"), [(False, ("delta", False)), (True, ("full", True))])
async def test_the_worker_maps_full_rebuild_to_a_full_replace_build(
    full_rebuild: bool, expected: tuple[str, bool]
) -> None:
    import app.workers.tasks.graphrag as graphrag
    from contexts.knowledge.domain.graphrag import BuildState
    from tests.unit.test_graphrag_build_busy import _Closable, _Gauge, _Session

    seen: dict[str, Any] = {}

    class _Builder:
        def __init__(self, **_k: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            from types import SimpleNamespace

            return SimpleNamespace(state=BuildState.IDLE, triples_written=0, entities_written=0)

    from types import SimpleNamespace

    repo = AsyncMock()
    repo.get.return_value = SimpleNamespace(id=uuid.uuid4())
    settings = SimpleNamespace(
        neo4j=SimpleNamespace(url="bolt://x", user="u", password="p"),
        qdrant=SimpleNamespace(url="http://x", api_key=""),
        graphrag=SimpleNamespace(build_concurrency=1),
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
        patch.object(graphrag, "GRAPHRAG_BUILD_STATE", _Gauge()),
    ):
        await graphrag.graphrag_build({}, config_id=str(uuid.uuid4()), full_rebuild=full_rebuild)
    assert (seen["mode"], seen["replace"]) == expected
