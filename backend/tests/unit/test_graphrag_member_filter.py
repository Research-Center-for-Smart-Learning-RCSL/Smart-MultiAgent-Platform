"""The per-member read filter on the retrieval path (R11.22, AC-3).

``docs/tasks/2026-10-12-graphrag-phase5b-member-filter`` §5.3: a group layer's
filter travels from the layer resolver through the provider to the retrieve
service and the traversal; under a filter, entities come from the visible
relations only. The Cypher predicate itself runs on real Neo4j in
``tests/integration/test_graphrag_member_filter_traverse.py``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_retrieve import GraphRagRetrieveService
from tests.unit.test_graphrag_retrieve import FakeNeo4j, FakeRepo, FakeVectors, _cfg, _factory


class _Hit:
    def __init__(self, entity: str) -> None:
        self.point_id = uuid.uuid4()
        self.score = 0.9
        self.entity = entity
        self.description = entity
        self.build_id = None


class _RecordingNeo4j(FakeNeo4j):
    def __init__(self, edges: list[dict[str, Any]]) -> None:
        super().__init__(edges)
        self.member_ids: list[Any] = []

    async def traverse(self, *, config_id, seed_entities, hops, member_ids=None):
        self.member_ids.append(member_ids)
        return await super().traverse(config_id=config_id, seed_entities=seed_entities, hops=hops)


def _service(neo4j: FakeNeo4j, seeds: list[str]) -> GraphRagRetrieveService:
    return GraphRagRetrieveService(
        None,  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=FakeVectors([_Hit(s) for s in seeds]),  # type: ignore[arg-type]
        embedder_factory=_factory,
        configs=FakeRepo(_cfg()),  # type: ignore[arg-type]
    )


_EDGE = {
    "subject": "alice",
    "relation": "knows",
    "object": "bob",
    "confidence": 0.9,
    "evidence_refs": [],
    "source_member_ids": ["member-b"],
}


@pytest.mark.asyncio
async def test_a_filter_reaches_the_traversal_and_shapes_the_entities() -> None:
    neo4j = _RecordingNeo4j([_EDGE])
    # "carol" is a seed only a hidden relation could support.
    bundle = await _service(neo4j, ["alice", "carol"]).query(
        config_id=uuid.uuid4(), text="q", member_ids=["member-b"]
    )
    assert neo4j.member_ids == [["member-b"]]
    assert bundle.entities == ("alice", "bob")
    assert [r.relation for r in bundle.relations] == ["knows"]


@pytest.mark.asyncio
async def test_without_a_filter_the_seeds_are_the_entities_as_before() -> None:
    neo4j = _RecordingNeo4j([_EDGE])
    bundle = await _service(neo4j, ["alice", "carol"]).query(config_id=uuid.uuid4(), text="q")
    assert neo4j.member_ids == [None]
    assert bundle.entities == ("alice", "carol")


@pytest.mark.asyncio
async def test_an_empty_filter_reads_nothing_and_touches_no_store() -> None:
    neo4j = _RecordingNeo4j([_EDGE])
    service = _service(neo4j, ["alice"])
    bundle = await service.query(config_id=uuid.uuid4(), text="q", member_ids=[])
    assert (bundle.entities, bundle.relations) == ((), ())
    assert neo4j.member_ids == []
    assert service._vectors.search_calls == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_query_layers_hands_each_layer_its_own_filter() -> None:
    from contexts.knowledge.application.graphrag_context_provider import GraphRagContextProvider

    room, group, workspace = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    seen: dict[uuid.UUID, Any] = {}

    class _Provider(GraphRagContextProvider):
        def __init__(self) -> None:
            pass

        async def _graphrag_query(self, config_id, queries, querying_agent_id=None, member_ids=None):
            seen[config_id] = member_ids
            return []

    await _Provider().query_layers(
        graphrag_config_ids=[room, group, workspace],
        member_filters={group: ("member-b",)},
        query_texts=["q"],
        querying_agent_id=uuid.uuid4(),
    )
    assert seen == {room: None, group: ("member-b",), workspace: None}


@pytest.mark.asyncio
async def test_the_turn_engine_passes_only_group_layer_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    import contexts.agents.application.runtime.turn_engine as te
    import contexts.knowledge.interfaces.facade as kfacade
    from contexts.knowledge.domain.graphrag import ConceptMapLayer

    room_cfg, group_cfg = _cfg(), _cfg()
    layers = [
        ConceptMapLayer(config=room_cfg),
        ConceptMapLayer(config=group_cfg, read_member_ids=("member-b",)),
    ]

    class _Facade:
        def __init__(self, _db: Any) -> None:
            pass

        async def resolve_graphrag_layers(self, *, agent_id: Any, chatroom_id: Any) -> Any:
            return layers

    calls: list[dict[str, Any]] = []

    class _GraphProvider:
        async def query_layers(self, **kwargs: Any) -> str:
            calls.append(kwargs)
            return "block"

    monkeypatch.setattr(kfacade, "KnowledgeFacade", _Facade)
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._db = object()  # type: ignore[attr-defined]
    engine._graphrag_provider = _GraphProvider()  # type: ignore[attr-defined]

    agent = type("A", (), {"id": uuid.uuid4()})()
    await engine._graphrag_context(agent, uuid.uuid4(), ["q"])  # type: ignore[arg-type]

    assert calls[0]["graphrag_config_ids"] == [room_cfg.id, group_cfg.id]
    assert calls[0]["member_filters"] == {group_cfg.id: ("member-b",)}
