"""The compaction-summary metadata contract (R9.09, R9.10, R13.26).

Four places read this shape — the model-facing history loader, the retention
purge, the repair command, and the writer itself — so a reader that drifts from
the writer fails silently: the purge simply stops matching, and nobody notices
until folded content outlives its retention horizon. These tests pin the shape
itself rather than any one reader's use of it.

The values are persisted in `messages.metadata` in every deployment, so a change
that breaks one of these assertions is a data migration, not a rename.
"""

from __future__ import annotations

import uuid

import pytest

from contexts.conversation.domain import compaction as c


def test_the_persisted_discriminators_are_what_is_already_in_the_database() -> None:
    assert c.COMPACT_SUMMARY_TYPE == "compact_summary"
    assert c.VOIDED_SUMMARY_TYPE == "compact_summary_voided"
    assert c.COMPACTED_IDS_KEY == "compacted_ids"
    assert c.PRODUCER_AGENT_ID_KEY == "producer_agent_id"
    assert c.ORIGINAL_COMPACTED_IDS_KEY == "original_compacted_ids"


def test_the_writer_round_trips_through_the_readers() -> None:
    # The property that actually matters: what `summary_metadata` writes is what
    # `compacted_ids` and `summary_producer` read back.
    folded = [uuid.uuid4(), uuid.uuid4()]
    producer = uuid.uuid4()

    meta = c.summary_metadata(message_ids=folded, producer_agent_id=producer)
    meta["type"] = c.COMPACT_SUMMARY_TYPE  # stamped by insert_system_message

    assert c.is_compact_summary(meta)
    assert c.compacted_ids(meta) == [str(f) for f in folded]
    assert c.summary_producer(meta) == str(producer)


def test_summary_metadata_omits_the_type() -> None:
    # `type` is service-stamped by `insert_system_message`; that is what keeps
    # it unforgeable by a client, so the writer must not supply it here.
    meta = c.summary_metadata(message_ids=[], producer_agent_id=uuid.uuid4())
    assert "type" not in meta


@pytest.mark.parametrize(
    "metadata",
    [None, {}, {"type": "rag_chunks"}, {"type": "compact_summary_voided"}, "not a dict", 42],
)
def test_only_a_live_summary_is_a_summary(metadata) -> None:
    assert c.is_compact_summary(metadata) is False
    assert c.compacted_ids(metadata) == []


@pytest.mark.parametrize("absent", [{}, {"producer_agent_id": None}, {"producer_agent_id": ""}])
def test_a_missing_and_a_null_producer_are_the_same_answer(absent) -> None:
    # Q-7: a pre-scoping row belongs to no one, and neither form may match a
    # reader - a str(uuid) is never None and never "".
    assert c.summary_producer(absent) is None


def test_compacted_ids_tolerates_a_malformed_list() -> None:
    # Read defensively: these rows are years old in some deployments and the
    # purge must not crash on one bad row and skip every room behind it.
    assert c.compacted_ids({"compacted_ids": None}) == []
    assert c.compacted_ids({"compacted_ids": ["a", 1]}) == ["a", "1"]
