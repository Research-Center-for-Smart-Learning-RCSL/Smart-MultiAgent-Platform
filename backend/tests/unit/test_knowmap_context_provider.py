"""Unit tests for KnowledgeMapContextProvider orchestration (Phase 3, WS3 / AC-4/AC-5).

Exercises the security core *through* :meth:`query` — the per-turn allowlist resolve,
the edge filter over the retrieved relations, entity-from-kept-relations, and the
degrade-to-``None`` contract — using the provider's ``_retrieve_relations`` unit seam
and mocked repositories. No live Neo4j/Qdrant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.knowledge.application.graphrag_retrieve import GraphRagRetrieveService
from contexts.knowledge.application.knowmap_context_provider import KnowledgeMapContextProvider
from contexts.knowledge.domain.graphrag import BuildState, GraphRagConfig, RelationEdge

_MOD = "contexts.knowledge.application.knowmap_context_provider"
_CONFIG_ID = uuid.uuid4()
_AGENT_ID = uuid.uuid4()


def _edge(subject: str, obj: str, *refs: str) -> RelationEdge:
    return RelationEdge(
        subject=subject, relation="rel", object=obj, confidence=0.9, evidence_refs=tuple(refs)
    )


def _ref(doc_id: uuid.UUID, idx: int = 0) -> str:
    return f"{doc_id}#{idx}"


class _Provider(KnowledgeMapContextProvider):
    """Provider with the Neo4j/Qdrant retrieval seam replaced by canned relations."""

    def __init__(self, raw: list[RelationEdge]) -> None:
        super().__init__(AsyncMock(), router=MagicMock())
        self._raw = raw

    async def _retrieve_relations(self, config_id, queries):  # type: ignore[override]
        return self._raw


def _patch_repos(*, allowed: list[uuid.UUID], excerpts: dict | None = None):
    doc_repo = MagicMock()
    doc_repo.return_value.allowed_document_ids = AsyncMock(return_value=allowed)
    chunk_repo = MagicMock()
    chunk_repo.return_value.get_excerpts = AsyncMock(return_value=excerpts or {})
    return patch.multiple(_MOD, KnowmapDocumentRepository=doc_repo, KnowmapChunkRepository=chunk_repo)


class TestQueryGuards:
    async def test_no_config_returns_none(self) -> None:
        out = await _Provider([]).query(knowmap_config_id=None, query_text="q", querying_agent_id=_AGENT_ID)
        assert out is None

    async def test_no_agent_returns_none(self) -> None:
        out = await _Provider([_edge("A", "B", _ref(uuid.uuid4()))]).query(
            knowmap_config_id=_CONFIG_ID, query_text="q", querying_agent_id=None
        )
        assert out is None

    async def test_blank_query_returns_none(self) -> None:
        out = await _Provider([]).query(
            knowmap_config_id=_CONFIG_ID, query_text="   ", querying_agent_id=_AGENT_ID
        )
        assert out is None

    async def test_empty_allowlist_returns_none_without_retrieving(self) -> None:
        prov = _Provider([_edge("A", "B", _ref(uuid.uuid4()))])
        with _patch_repos(allowed=[]):
            out = await prov.query(knowmap_config_id=_CONFIG_ID, query_text="q", querying_agent_id=_AGENT_ID)
        assert out is None


class TestQueryEdgeFilter:
    async def test_fully_allowed_relation_surfaces_entities(self) -> None:
        d = uuid.uuid4()
        prov = _Provider([_edge("Alice", "Acme", _ref(d))])
        with _patch_repos(allowed=[d], excerpts={(d, 0): "Alice works at Acme."}):
            out = await prov.query(knowmap_config_id=_CONFIG_ID, query_text="q", querying_agent_id=_AGENT_ID)
        assert out is not None
        assert "Alice" in out
        assert "Acme" in out

    async def test_relation_with_denied_source_is_absent(self) -> None:
        # AC-5: the retrieval surfaces a relation whose second source doc is NOT in
        # the agent's allowlist -> it (and its entities) must not appear.
        allowed_doc, denied_doc = uuid.uuid4(), uuid.uuid4()
        prov = _Provider([_edge("Public", "Secret", _ref(allowed_doc), _ref(denied_doc))])
        with _patch_repos(allowed=[allowed_doc]):
            out = await prov.query(knowmap_config_id=_CONFIG_ID, query_text="q", querying_agent_id=_AGENT_ID)
        assert out is None

    async def test_mixed_keeps_allowed_drops_denied(self) -> None:
        keep_doc, denied_doc = uuid.uuid4(), uuid.uuid4()
        prov = _Provider(
            [
                _edge("Alice", "Acme", _ref(keep_doc)),
                _edge("Mole", "Dossier", _ref(keep_doc), _ref(denied_doc)),
            ]
        )
        with _patch_repos(allowed=[keep_doc], excerpts={(keep_doc, 0): "Alice at Acme."}):
            out = await prov.query(knowmap_config_id=_CONFIG_ID, query_text="q", querying_agent_id=_AGENT_ID)
        assert out is not None
        assert "Alice" in out
        assert "Acme" in out
        # The denied relation's entities never leak.
        assert "Mole" not in out
        assert "Dossier" not in out


# ---------------------------------------------------------------------------
# F-10 §8.3 — the Knowledge Map read path inherits the single build-state gate in
# GraphRagRetrieveService.query. Wire a REAL retrieve service through the
# `_retrieve_relations` seam over fakes so the gate is exercised end to end.
# ---------------------------------------------------------------------------


class _GateVectors:
    def __init__(self) -> None:
        self.searched = False

    async def search_entities(self, **_: Any) -> list[Any]:
        self.searched = True

        class _H:
            point_id = uuid.uuid4()
            score = 0.9
            entity = "alice"
            description = "d"
            build_id = None

        return [_H()]


class _GateNeo4j:
    def __init__(self, ref: str) -> None:
        self.traversed = False
        self._ref = ref

    async def traverse(self, *, config_id: Any, seed_entities: Any, hops: Any) -> list[dict[str, Any]]:
        self.traversed = True
        return [
            {
                "subject": "alice",
                "relation": "rel",
                "object": "bob",
                "confidence": 0.9,
                "evidence_msg_ids": [self._ref],
            }
        ]


async def _gate_factory(_cfg: Any) -> Any:
    class _E:
        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 3 for _ in texts]

    return _E()


def _gate_cfg(state: BuildState) -> GraphRagConfig:
    return GraphRagConfig(
        id=_CONFIG_ID,
        project_id=uuid.uuid4(),
        agent_id=None,
        builder_key_group_id=uuid.uuid4(),
        trigger_config={},
        last_build_at=None,
        last_build_state=state,
        last_build_error=None,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


class _GateRepo:
    def __init__(self, cfg: GraphRagConfig) -> None:
        self._cfg = cfg

    async def get(self, _id: uuid.UUID, *, include_deleted: bool = False) -> GraphRagConfig:
        return self._cfg


class _GateProvider(KnowledgeMapContextProvider):
    """Provider whose retrieval seam runs the real shared retrieve service so the
    F-10 build-state gate is exercised through the knowmap path."""

    def __init__(self, *, state: BuildState, vectors: _GateVectors, neo4j: _GateNeo4j) -> None:
        super().__init__(AsyncMock(), router=MagicMock())
        self._state = state
        self._gate_vectors = vectors
        self._gate_neo4j = neo4j

    async def _retrieve_relations(self, config_id, queries):  # type: ignore[override]
        svc = GraphRagRetrieveService(
            None,  # type: ignore[arg-type]
            neo4j=self._gate_neo4j,  # type: ignore[arg-type]
            vector_store=self._gate_vectors,  # type: ignore[arg-type]
            embedder_factory=_gate_factory,
            configs=_GateRepo(_gate_cfg(self._state)),  # type: ignore[arg-type]
        )
        relations: list[RelationEdge] = []
        for query in queries:
            bundle = await svc.query(config_id=config_id, text=query, querying_agent_id=None)
            relations.extend(bundle.relations)
        return relations


class TestSharedReadGate:
    async def test_knowmap_gated_when_config_mid_2pc(self) -> None:
        # AC-3: a knowmap config in neo4j_committed returns None via the shared
        # GraphRagRetrieveService gate, without seeding Qdrant or traversing Neo4j.
        doc = uuid.uuid4()
        vectors, neo4j = _GateVectors(), _GateNeo4j(_ref(doc))
        prov = _GateProvider(state=BuildState.NEO4J_COMMITTED, vectors=vectors, neo4j=neo4j)
        with _patch_repos(allowed=[doc]):
            out = await prov.query(knowmap_config_id=_CONFIG_ID, query_text="q", querying_agent_id=_AGENT_ID)
        assert out is None
        assert not vectors.searched
        assert not neo4j.traversed

    async def test_knowmap_retrieves_when_idle(self) -> None:
        # Guard: an idle knowmap config retrieves normally through the same seam.
        doc = uuid.uuid4()
        vectors, neo4j = _GateVectors(), _GateNeo4j(_ref(doc))
        prov = _GateProvider(state=BuildState.IDLE, vectors=vectors, neo4j=neo4j)
        with _patch_repos(allowed=[doc], excerpts={(doc, 0): "Alice rel Bob."}):
            out = await prov.query(knowmap_config_id=_CONFIG_ID, query_text="q", querying_agent_id=_AGENT_ID)
        assert out is not None
        assert "alice" in out.lower()
        assert vectors.searched
        assert neo4j.traversed
