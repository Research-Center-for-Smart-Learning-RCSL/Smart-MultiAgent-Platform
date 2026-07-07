"""Unit tests for GraphRAG triple parsing, incl. entity-type normalization (L1)."""

from __future__ import annotations

import uuid

from contexts.knowledge.infrastructure.triple_extractor import _norm_type, _parse_triples


def test_parses_types_into_controlled_vocabulary() -> None:
    mid = uuid.uuid4()
    raw = (
        '[{"subject": "Alice", "relation": "works_at", "object": "Acme", '
        '"subject_type": "Person", "object_type": "organization", '
        f'"confidence": 0.8, "evidence_msg_ids": ["{mid}"]}}]'
    )
    triples = _parse_triples(raw)
    assert len(triples) == 1
    tr = triples[0]
    assert tr.subject == "Alice"
    assert tr.subject_type == "person"  # lower-cased into the vocabulary
    assert tr.object_type == "organization"
    assert tr.evidence_refs == (str(mid),)


def test_unknown_type_maps_to_other_and_missing_to_blank() -> None:
    raw = '[{"subject": "x", "relation": "r", "object": "y", ' '"subject_type": "alien", "confidence": 0.5}]'
    triples = _parse_triples(raw)
    assert len(triples) == 1
    assert triples[0].subject_type == "other"  # not in vocabulary -> other
    assert triples[0].object_type == ""  # absent -> unknown


def test_evidence_refs_drop_null_blank_and_oversized() -> None:
    mid = uuid.uuid4()
    long_ref = "x" * 200  # exceeds MAX_EVIDENCE_REF_LEN
    raw = (
        '[{"subject": "a", "relation": "r", "object": "b", '
        f'"evidence_msg_ids": ["{mid}", null, "  ", 12345, "{long_ref}"]}}]'
    )
    triples = _parse_triples(raw)
    assert len(triples) == 1
    # Only the valid, length-bounded string ref survives; a JSON null never
    # becomes the literal "None", numbers and oversized strings are dropped.
    assert triples[0].evidence_refs == (str(mid),)


def test_norm_type_helper() -> None:
    assert _norm_type("PERSON") == "person"
    assert _norm_type("  Concept ") == "concept"
    assert _norm_type("nonsense") == "other"
    assert _norm_type("") == ""
    assert _norm_type(None) == ""
    assert _norm_type(123) == ""
