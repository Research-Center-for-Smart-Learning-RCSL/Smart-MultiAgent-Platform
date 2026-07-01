"""Unit tests for :class:`GraphRagRetrieveService` (E.8 / R11.06)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from contexts.knowledge.application.graphrag_retrieve import (
    GraphRagRetrieveService,
)
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig


class FakeRepo:
    def __init__(self, cfg: GraphRagConfig) -> None:
        self._cfg = cfg

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False):
        return self._cfg


class FakeVectors:
    def __init__(self, hits: list[Any]) -> None:
        self.hits = hits

    async def search_entities(self, **_: Any):
        return self.hits


class FakeNeo4j:
    def __init__(self, edges: list[dict[str, Any]]) -> None:
        self.edges = edges
        self.traverse_calls: list[tuple[list[str], int]] = []

    async def traverse(self, *, config_id, seed_entities, hops):
        self.traverse_calls.append((list(seed_entities), hops))
        return list(self.edges)

    async def snapshot_subgraph(self, **_: Any):
        return {"edges": []}

    async def apply_triples(self, **_: Any):
        return 0

    async def delete_by_build(self, **_: Any) -> None:
        return None

    async def delete_all(self, **_: Any) -> None:
        return None

    async def restore_from_snapshot(self, **_: Any) -> None:
        return None


class FakeEmbedder:
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 3 for _ in texts]


async def _factory(cfg):
    return FakeEmbedder()


def _cfg() -> GraphRagConfig:
    return GraphRagConfig(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        builder_key_group_id=uuid.uuid4(),
        trigger_config={},
        last_build_at=None,
        last_build_state=BuildState.IDLE,
        last_build_error=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_hybrid_query_returns_bundle() -> None:
    cfg = _cfg()
    msg_id = uuid.uuid4()

    class _Hit:
        def __init__(self, entity: str) -> None:
            self.point_id = uuid.uuid4()
            self.score = 0.9
            self.entity = entity
            self.description = f"desc {entity}"
            self.build_id = None

    vectors = FakeVectors([_Hit("alice"), _Hit("bob")])
    neo4j = FakeNeo4j(
        edges=[
            {
                "subject": "alice",
                "relation": "knows",
                "object": "bob",
                "confidence": 0.9,
                "evidence_msg_ids": [str(msg_id)],
            },
        ],
    )
    service = GraphRagRetrieveService(
        None,  # type: ignore[arg-type]
        neo4j=neo4j,
        vector_store=vectors,  # type: ignore[arg-type]
        embedder_factory=_factory,
        evidence_fetcher=lambda ids: _ev_fetcher(ids),
    )
    # Patch the config repo.
    service._configs = FakeRepo(cfg)  # type: ignore[assignment, attr-defined]

    bundle = await service.query(config_id=cfg.id, text="who knows bob?", top_k=5, hops=2)

    assert bundle.entities == ("alice", "bob")
    assert len(bundle.relations) == 1
    rel = bundle.relations[0]
    assert rel.subject == "alice"
    assert rel.object == "bob"
    assert rel.evidence_msg_ids == (msg_id,)
    assert bundle.evidence_excerpts == ("excerpt-0",)
    assert neo4j.traverse_calls == [(["alice", "bob"], 2)]


async def _ev_fetcher(ids: list[uuid.UUID]) -> list[str]:
    return [f"excerpt-{i}" for i in range(len(ids))]


@pytest.mark.asyncio
async def test_context_provider_merges_multi_query_bundles() -> None:
    from contexts.knowledge.application.graphrag_context_provider import GraphRagContextProvider
    from contexts.knowledge.domain.graphrag import GraphRagBundle, RelationEdge

    class _Provider(GraphRagContextProvider):
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def _graphrag_query(self, config_id: uuid.UUID, query: str):
            self.queries.append(query)
            if query == "first":
                return GraphRagBundle(
                    entities=("alice",),
                    relations=(
                        RelationEdge(
                            subject="alice",
                            relation="owns",
                            object="roadmap",
                            confidence=0.7,
                            evidence_msg_ids=(),
                        ),
                    ),
                    evidence_excerpts=("excerpt A",),
                )
            return GraphRagBundle(
                entities=("roadmap",),
                relations=(
                    RelationEdge(
                        subject="roadmap",
                        relation="targets",
                        object="q3",
                        confidence=0.9,
                        evidence_msg_ids=(),
                    ),
                ),
                evidence_excerpts=("excerpt B",),
            )

    provider = _Provider()
    text = await provider.query(graphrag_config_id=uuid.uuid4(), query_texts=["first", "second"])

    assert provider.queries == ["first", "second"]
    assert text is not None
    assert "alice" in text
    assert "roadmap" in text
    assert "targets" in text


@pytest.mark.asyncio
async def test_empty_vector_hits_returns_empty_bundle() -> None:
    cfg = _cfg()
    service = GraphRagRetrieveService(
        None,  # type: ignore[arg-type]
        neo4j=FakeNeo4j(edges=[]),
        vector_store=FakeVectors([]),  # type: ignore[arg-type]
        embedder_factory=_factory,
    )
    service._configs = FakeRepo(cfg)  # type: ignore[assignment, attr-defined]

    bundle = await service.query(config_id=cfg.id, text="empty")
    assert bundle.entities == ()
    assert bundle.relations == ()


def test_bundle_serialises_under_2kb_cap() -> None:
    from contexts.knowledge.domain.graphrag import (
        GraphRagBundle,
        RelationEdge,
    )

    huge = "x" * 4000
    bundle = GraphRagBundle(
        entities=("a", "b"),
        relations=(
            RelationEdge(
                subject="a",
                relation="r",
                object="b",
                confidence=1.0,
                evidence_msg_ids=(),
            ),
        ),
        evidence_excerpts=(huge,),
    )
    payload = bundle.as_system_message()
    import json

    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= 2048
    assert payload["metadata"]["type"] == "graphrag"
