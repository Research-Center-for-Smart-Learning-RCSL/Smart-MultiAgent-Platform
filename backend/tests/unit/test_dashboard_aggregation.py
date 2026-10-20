"""Current-round watchlist composition and attention rules ([R33.06])."""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.aggregation_service import AggregationService
from contexts.activities.domain.models import RoundSubject, RoundSubjectCount, SubjectKind


def _uid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_watchlist_includes_zero_invalid_idle_and_active_participants() -> None:
    service = AggregationService(AsyncSession())
    room_id = _uid()
    activation_id = _uid()
    now = dt.datetime(2026, 10, 20, 10, 0, tzinfo=dt.UTC)
    started_at = now - dt.timedelta(minutes=30)
    zero, invalid_only, idle, active_a, active_b = (_uid() for _ in range(5))
    counts = [
        RoundSubjectCount(activation_id, SubjectKind.USER, idle, 1, now - dt.timedelta(minutes=12)),
        RoundSubjectCount(activation_id, SubjectKind.USER, active_a, 1, now - dt.timedelta(minutes=1)),
        RoundSubjectCount(activation_id, SubjectKind.USER, active_b, 2, now - dt.timedelta(minutes=1)),
    ]
    roster = {
        room_id: {
            (zero, SubjectKind.USER.value),
            (invalid_only, SubjectKind.USER.value),
            (idle, SubjectKind.USER.value),
            (active_a, SubjectKind.USER.value),
            (active_b, SubjectKind.USER.value),
        }
    }
    with patch.object(service._repo, "round_subject_counts", return_value=counts):
        entries = await service.watchlist_for_rounds(
            rounds=[(room_id, activation_id, started_at, True)],
            roster=roster,
            session_subjects={},
            now_at=now,
        )

    by_id = {entry.subject_id: entry for entry in entries}
    assert len(by_id) == 5
    assert by_id[zero].reason == "no_valid_in_round"
    assert by_id[zero].valid_count == 0
    assert by_id[invalid_only].reason == "no_valid_in_round"
    assert by_id[idle].reason == "idle"
    assert by_id[active_a].reason is None
    assert by_id[active_b].reason is None
    assert [entry.needs_attention for entry in entries] == [True, True, True, False, False]


@pytest.mark.asyncio
async def test_group_round_lists_groups_only_and_ended_round_is_never_idle() -> None:
    service = AggregationService(AsyncSession())
    room_id = _uid()
    activation_id = _uid()
    group_id = _uid()
    user_id = _uid()
    now = dt.datetime(2026, 10, 20, 10, 0, tzinfo=dt.UTC)
    group = RoundSubject(SubjectKind.MEMBER_GROUP, group_id)
    counts = [
        RoundSubjectCount(activation_id, SubjectKind.MEMBER_GROUP, group_id, 1, now - dt.timedelta(hours=1))
    ]
    with patch.object(service._repo, "round_subject_counts", return_value=counts):
        entries = await service.watchlist_for_rounds(
            rounds=[(room_id, activation_id, now - dt.timedelta(hours=2), False)],
            roster={room_id: {(user_id, SubjectKind.USER.value)}},
            session_subjects={activation_id: {group}},
            now_at=now,
        )

    # Q-11 / AC-17: validity exists per group only, so the student present in
    # the room must not become a row of their own.
    assert [(entry.subject_kind, entry.subject_id) for entry in entries] == [
        (SubjectKind.MEMBER_GROUP, group_id)
    ]
    assert entries[0].reason is None
    assert user_id not in {entry.subject_id for entry in entries}


@pytest.mark.asyncio
async def test_empty_rounds_skip_aggregation_query() -> None:
    service = AggregationService(AsyncSession())
    with patch.object(service._repo, "round_subject_counts") as counts:
        entries = await service.watchlist_for_rounds(rounds=[], roster={}, session_subjects={})
    assert entries == []
    counts.assert_not_called()
