"""Classification rules of the compaction-summary repair command.

The pass is dry-run by default and only ever edits summary-row metadata, so the
part worth pinning is which rows it decides are bad — a false positive voids a
legitimate summary and costs the room a re-fold.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import smap.maintenance.repair_compaction_summaries as repair


def _row(*, content="SUMMARY", producer=None, covered=(), room=None, msg_id=None):
    meta: dict = {"type": "compact_summary", "compacted_ids": [str(c) for c in covered]}
    if producer is not None:
        meta["producer_agent_id"] = str(producer)
    return SimpleNamespace(
        id=msg_id or uuid.uuid4(),
        chatroom_id=room or uuid.uuid4(),
        content_md=content,
        metadata=meta,
    )


def test_is_armed_defaults_to_false(monkeypatch) -> None:
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    assert repair.is_armed() is False


def test_is_armed_ignores_an_unrecognised_value(monkeypatch) -> None:
    # Anything but an explicit truthy value is a dry run: a typo must not arm a
    # pass that edits rows.
    monkeypatch.setenv(repair._ARMED_ENV, "maybe")
    assert repair.is_armed() is False


def test_is_armed_accepts_an_explicit_truthy_value(monkeypatch) -> None:
    monkeypatch.setenv(repair._ARMED_ENV, "1")
    assert repair.is_armed() is True


def test_empty_summaries_are_voided_regardless_of_producer(monkeypatch) -> None:
    # Already inert as a projection, but the row still renders as a blank system
    # divider users can see and cannot read.
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    rows = [_row(content=""), _row(content="   "), _row(content="\n\t", producer=uuid.uuid4())]

    report = repair._classify(rows)

    assert len(report.empty) == 3
    assert report.overlapping == []
    assert report.producerless == 0  # empty wins; the row goes either way


def test_producerless_summaries_are_counted_not_voided(monkeypatch) -> None:
    # Q-7: the loader already treats them as belonging to no one, so the room
    # has its history back. Voiding would destroy summary text users can read.
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    rows = [_row(covered=[uuid.uuid4()]), _row(covered=[uuid.uuid4()])]

    report = repair._classify(rows)

    assert report.producerless == 2
    assert report.would_void == 0


def test_overlap_within_one_producer_voids_the_later_summary(monkeypatch) -> None:
    # Legitimate compaction never overlaps - choose_range_to_compact starts at
    # the first un-compacted message and stops at any prior summary - so an
    # intersection within one producer is evidence of the lock race.
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    room, producer = uuid.uuid4(), uuid.uuid4()
    m1, m2, m3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    earlier = _row(room=room, producer=producer, covered=[m1, m2])
    later = _row(room=room, producer=producer, covered=[m2, m3])

    report = repair._classify([earlier, later])  # rows arrive oldest-first

    assert [v.message_id for v in report.overlapping] == [later.id]


def test_two_producers_folding_the_same_range_is_not_an_overlap(monkeypatch) -> None:
    # Under per-agent scoping each agent folds its own view, so the same
    # messages appearing in two producers' summaries is normal, not a race.
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    room = uuid.uuid4()
    m1, m2 = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(room=room, producer=uuid.uuid4(), covered=[m1, m2]),
        _row(room=room, producer=uuid.uuid4(), covered=[m1, m2]),
    ]

    report = repair._classify(rows)

    assert report.overlapping == []
    assert report.would_void == 0


def test_the_same_producer_in_two_rooms_is_not_an_overlap(monkeypatch) -> None:
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    producer = uuid.uuid4()
    m1 = uuid.uuid4()
    rows = [
        _row(room=uuid.uuid4(), producer=producer, covered=[m1]),
        _row(room=uuid.uuid4(), producer=producer, covered=[m1]),
    ]

    report = repair._classify(rows)

    assert report.overlapping == []


def test_consecutive_non_overlapping_folds_are_left_alone(monkeypatch) -> None:
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    room, producer = uuid.uuid4(), uuid.uuid4()
    rows = [
        _row(room=room, producer=producer, covered=[uuid.uuid4(), uuid.uuid4()]),
        _row(room=room, producer=producer, covered=[uuid.uuid4()]),
    ]

    report = repair._classify(rows)

    assert report.would_void == 0
    assert report.examined == 2


def test_report_is_dry_run_unless_armed(monkeypatch) -> None:
    monkeypatch.delenv(repair._ARMED_ENV, raising=False)
    assert repair._classify([]).dry_run is True

    monkeypatch.setenv(repair._ARMED_ENV, "true")
    assert repair._classify([]).dry_run is False
