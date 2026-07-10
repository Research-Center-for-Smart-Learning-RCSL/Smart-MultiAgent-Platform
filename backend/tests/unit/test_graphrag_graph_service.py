"""GraphRagGraphService — characterization tests written alongside extracting
the shared contexts.knowledge.domain.graph_view_assembly helper (code review
finding: this service previously shipped with zero test coverage, per
test_knowmap_graph_service.py's own note; added here as the safety net for
touching its internals). Mirrors test_knowmap_graph_service.py's contract
exactly, since both services now share the same assembly function.
"""

from __future__ import annotations

import uuid

import pytest

import contexts.knowledge.application.graphrag_graph_service as gr_mod
from contexts.knowledge.domain.errors import GraphRagConfigNotFound


class _FakeCfg:
    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id


class _FakeRepo:
    def __init__(self, cfg: _FakeCfg | None) -> None:
        self._cfg = cfg

    def __call__(self, _db):
        return self

    async def get(self, _config_id, *, include_deleted: bool = False):
        return self._cfg


class _FakeDriver:
    last_limit: int | None = None

    def __init__(self, **_kw) -> None:
        self.closed = False

    async def fetch_graph(self, *, config_id, limit):
        _FakeDriver.last_limit = limit
        return {
            "nodes": [{"name": "alice", "build_id": "b1", "type": "person", "degree": 2}],
            "edges": [
                {"subject": "alice", "relation": "knows", "object": "bob", "confidence": 0.9},
            ],
            "truncated": False,
        }

    async def close(self) -> None:
        self.closed = True


def _patch_common(monkeypatch: pytest.MonkeyPatch, *, cfg: _FakeCfg | None) -> None:
    monkeypatch.setattr(gr_mod, "GraphRagConfigRepository", _FakeRepo(cfg))
    monkeypatch.setattr(gr_mod, "Neo4jAsyncDriver", _FakeDriver)

    class _FakeSettings:
        class neo4j:  # noqa: N801 - mirrors settings.neo4j attribute access
            url = "bolt://fake"
            user = "u"
            password = "p"

    monkeypatch.setattr("app.config.settings.get_settings", lambda: _FakeSettings())


@pytest.mark.asyncio
async def test_get_graph_assembles_nodes_and_edges_self_consistently(monkeypatch) -> None:
    project_id = uuid.uuid4()
    _patch_common(monkeypatch, cfg=_FakeCfg(project_id))

    service = gr_mod.GraphRagGraphService(db=None)  # type: ignore[arg-type]
    config_id = uuid.uuid4()
    view = await service.get_graph(config_id=config_id)

    assert view.config_id == config_id
    assert view.project_id == project_id
    assert view.truncated is False
    names = {n.name for n in view.nodes}
    # "bob" only appears as an edge endpoint, never in the node query's result —
    # the self-consistency fill-in must still surface it as a zero-degree node.
    assert names == {"alice", "bob"}
    bob = next(n for n in view.nodes if n.name == "bob")
    assert bob.degree == 0
    assert len(view.edges) == 1
    assert view.edges[0].source == "alice"
    assert view.edges[0].target == "bob"


@pytest.mark.asyncio
async def test_get_graph_missing_config_raises_not_found(monkeypatch) -> None:
    _patch_common(monkeypatch, cfg=None)

    service = gr_mod.GraphRagGraphService(db=None)  # type: ignore[arg-type]
    with pytest.raises(GraphRagConfigNotFound):
        await service.get_graph(config_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_get_graph_clamps_limit_to_bounds(monkeypatch) -> None:
    _patch_common(monkeypatch, cfg=_FakeCfg(uuid.uuid4()))
    service = gr_mod.GraphRagGraphService(db=None)  # type: ignore[arg-type]

    await service.get_graph(config_id=uuid.uuid4(), limit=999_999)
    assert _FakeDriver.last_limit == gr_mod.MAX_GRAPH_LIMIT

    await service.get_graph(config_id=uuid.uuid4(), limit=-5)
    assert _FakeDriver.last_limit == 1
