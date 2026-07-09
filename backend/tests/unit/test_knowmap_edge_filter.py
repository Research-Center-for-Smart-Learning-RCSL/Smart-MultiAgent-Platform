"""Unit tests for the Knowledge Map allowlist edge filter — the security core
(Phase 3, Q-2 / AC-4 / AC-5). Pure functions, no I/O."""

from __future__ import annotations

import uuid

from contexts.knowledge.application.knowmap_context_provider import (
    decode_ref,
    decode_ref_document_id,
    entities_from_relations,
    filter_relations_by_allowlist,
    relation_source_documents,
)
from contexts.knowledge.domain.graphrag import RelationEdge


def _edge(subject: str, obj: str, *refs: str, relation: str = "rel") -> RelationEdge:
    return RelationEdge(
        subject=subject,
        relation=relation,
        object=obj,
        confidence=0.9,
        evidence_refs=tuple(refs),
    )


def _ref(doc_id: uuid.UUID, chunk_idx: int) -> str:
    return f"{doc_id}#{chunk_idx}"


class TestDecoders:
    def test_decode_document_id(self) -> None:
        d = uuid.uuid4()
        assert decode_ref_document_id(_ref(d, 3)) == d

    def test_decode_document_id_unparseable(self) -> None:
        assert decode_ref_document_id("not-a-uuid#1") is None
        assert decode_ref_document_id("garbage") is None

    def test_decode_ref_roundtrip(self) -> None:
        d = uuid.uuid4()
        assert decode_ref(_ref(d, 7)) == (d, 7)

    def test_decode_ref_missing_separator_or_bad_index(self) -> None:
        assert decode_ref(str(uuid.uuid4())) is None  # no '#'
        assert decode_ref(f"{uuid.uuid4()}#notint") is None


class TestRelationSourceDocuments:
    def test_collects_distinct_doc_ids(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        edge = _edge("A", "B", _ref(a, 0), _ref(a, 5), _ref(b, 2))
        assert relation_source_documents(edge) == {a, b}

    def test_unparseable_ref_poisons_the_whole_edge(self) -> None:
        a = uuid.uuid4()
        edge = _edge("A", "B", _ref(a, 0), "bad#1")
        # A single unverifiable ref -> None so the caller drops the edge (fail closed).
        assert relation_source_documents(edge) is None

    def test_no_refs_returns_empty_set(self) -> None:
        assert relation_source_documents(_edge("A", "B")) == set()


class TestFilterRelationsByAllowlist:
    def test_all_sources_allowed_is_kept(self) -> None:
        a, b = uuid.uuid4(), uuid.uuid4()
        edge = _edge("A", "B", _ref(a, 0), _ref(b, 1))
        kept = filter_relations_by_allowlist([edge], {a, b})
        assert kept == [edge]

    def test_one_denied_source_hides_the_edge(self) -> None:
        # AC-5: a relation with one denied source document is absent even though
        # its other sources are allowed (provenance-leak test).
        allowed, denied = uuid.uuid4(), uuid.uuid4()
        edge = _edge("A", "B", _ref(allowed, 0), _ref(denied, 3))
        assert filter_relations_by_allowlist([edge], {allowed}) == []

    def test_empty_allowlist_grants_nothing(self) -> None:
        a = uuid.uuid4()
        edge = _edge("A", "B", _ref(a, 0))
        assert filter_relations_by_allowlist([edge], set()) == []

    def test_relation_without_provenance_is_dropped(self) -> None:
        # No evidence refs -> provenance cannot be confirmed -> drop (secure default).
        assert filter_relations_by_allowlist([_edge("A", "B")], {uuid.uuid4()}) == []

    def test_relation_with_unparseable_ref_is_dropped(self) -> None:
        a = uuid.uuid4()
        edge = _edge("A", "B", _ref(a, 0), "corrupt")
        assert filter_relations_by_allowlist([edge], {a}) == []

    def test_mixed_batch_keeps_only_fully_allowed(self) -> None:
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        keep = _edge("A", "B", _ref(a, 0))
        drop = _edge("C", "D", _ref(a, 0), _ref(c, 1))
        kept = filter_relations_by_allowlist([keep, drop], {a, b})
        assert kept == [keep]


class TestEntitiesFromRelations:
    def test_entities_surface_only_via_kept_relations(self) -> None:
        # AC-4: an entity appears only via a visible relation, in first-seen order.
        e1 = _edge("Alice", "Acme")
        e2 = _edge("Acme", "Bob")
        assert entities_from_relations([e1, e2]) == ["Alice", "Acme", "Bob"]

    def test_blank_endpoints_are_skipped(self) -> None:
        assert entities_from_relations([_edge("", "X")]) == ["X"]

    def test_no_relations_no_entities(self) -> None:
        assert entities_from_relations([]) == []
