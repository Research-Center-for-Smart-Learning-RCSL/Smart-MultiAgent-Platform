"""Unit tests for KnowledgeMapContextProvider orchestration (Phase 3, WS3 / AC-4/AC-5).

Exercises the security core *through* :meth:`query` — the per-turn allowlist resolve,
the edge filter over the retrieved relations, entity-from-kept-relations, and the
degrade-to-``None`` contract — using the provider's ``_retrieve_relations`` unit seam
and mocked repositories. No live Neo4j/Qdrant.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.knowledge.application.knowmap_context_provider import KnowledgeMapContextProvider
from contexts.knowledge.domain.graphrag import RelationEdge

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
