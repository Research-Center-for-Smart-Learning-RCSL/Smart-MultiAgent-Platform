"""Unit tests for member-provenance attribution (Phase 2b WS1, R11.22).

``attach_member_provenance`` is the pure seam that turns per-message source
member ids into the ``source_member_ids`` set on a relation. It is the write
side of AC-2: a relation two members independently stated carries both, and a
relation whose evidence resolves to no member stays untagged (never invented).
"""

from __future__ import annotations

import uuid

from contexts.knowledge.application.graphrag_builder import attach_member_provenance
from contexts.knowledge.domain.graphrag import Triple


def _triple(*evidence: str) -> Triple:
    return Triple(
        subject="alice",
        relation="knows",
        object="bob",
        confidence=0.9,
        evidence_refs=tuple(evidence),
    )


def test_two_members_both_recorded_sorted() -> None:
    m1, m2 = str(uuid.uuid4()), str(uuid.uuid4())
    e1, e2 = "msg-1", "msg-2"
    out = attach_member_provenance(_triple(e1, e2), {e1: m1, e2: m2})
    assert out.source_member_ids == tuple(sorted([m1, m2]))


def test_single_member_recorded() -> None:
    m1 = str(uuid.uuid4())
    out = attach_member_provenance(_triple("msg-1"), {"msg-1": m1})
    assert out.source_member_ids == (m1,)


def test_duplicate_member_deduped() -> None:
    m1 = str(uuid.uuid4())
    # Two evidence messages from the same member collapse to a single tag.
    out = attach_member_provenance(_triple("msg-1", "msg-2"), {"msg-1": m1, "msg-2": m1})
    assert out.source_member_ids == (m1,)


def test_unresolved_evidence_leaves_triple_untagged() -> None:
    original = _triple("msg-x")
    out = attach_member_provenance(original, {"msg-other": str(uuid.uuid4())})
    # No matching evidence -> the exact same triple (untagged) is returned.
    assert out is original
    assert out.source_member_ids == ()


def test_empty_map_is_noop() -> None:
    original = _triple("msg-1")
    assert attach_member_provenance(original, {}) is original
