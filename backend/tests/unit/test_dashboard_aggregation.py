"""Unit tests for teacher dashboard aggregation logic ([R33.01])."""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from contexts.activities.application.aggregation_service import AggregationService
from contexts.activities.domain.models import WatchlistEntry


def _uid() -> uuid.UUID:
    return uuid.uuid4()


class TestWatchlistMedianComputation:
    """AC-3: watchlist returns participants at or below median."""

    @pytest.mark.asyncio
    async def test_all_equal_submissions_returns_all(self) -> None:
        db = AsyncMock()
        svc = AggregationService(db)
        room = _uid()
        now = dt.datetime.now(dt.UTC)
        raw = [("u:aaaaaaaa", 3, now), ("u:bbbbbbbb", 3, now), ("u:cccccccc", 3, now)]
        with patch.object(svc._repo, "participant_submission_counts", return_value=raw):
            result = await svc.watchlist_for_rooms(chatroom_ids=[room])
        assert len(result) == 3
        assert all(isinstance(e, WatchlistEntry) for e in result)

    @pytest.mark.asyncio
    async def test_below_median_filtered(self) -> None:
        db = AsyncMock()
        svc = AggregationService(db)
        room = _uid()
        now = dt.datetime.now(dt.UTC)
        raw = [
            ("u:aaaaaaaa", 1, now),
            ("u:bbbbbbbb", 5, now),
            ("u:cccccccc", 10, now),
        ]
        with patch.object(svc._repo, "participant_submission_counts", return_value=raw):
            result = await svc.watchlist_for_rooms(chatroom_ids=[room])
        codes = [e.subject_code for e in result]
        assert "u:aaaaaaaa" in codes
        assert "u:bbbbbbbb" in codes
        assert "u:cccccccc" not in codes

    @pytest.mark.asyncio
    async def test_zero_submissions_always_included(self) -> None:
        db = AsyncMock()
        svc = AggregationService(db)
        room = _uid()
        now = dt.datetime.now(dt.UTC)
        raw = [
            ("u:aaaaaaaa", 0, None),
            ("u:bbbbbbbb", 5, now),
            ("u:cccccccc", 10, now),
        ]
        with patch.object(svc._repo, "participant_submission_counts", return_value=raw):
            result = await svc.watchlist_for_rooms(chatroom_ids=[room])
        codes = [e.subject_code for e in result]
        assert "u:aaaaaaaa" in codes

    @pytest.mark.asyncio
    async def test_empty_rooms_returns_empty(self) -> None:
        db = AsyncMock()
        svc = AggregationService(db)
        with patch.object(svc._repo, "participant_submission_counts", return_value=[]):
            result = await svc.watchlist_for_rooms(chatroom_ids=[_uid()])
        assert result == []

    @pytest.mark.asyncio
    async def test_single_participant_included(self) -> None:
        db = AsyncMock()
        svc = AggregationService(db)
        now = dt.datetime.now(dt.UTC)
        raw = [("u:aaaaaaaa", 2, now)]
        with patch.object(svc._repo, "participant_submission_counts", return_value=raw):
            result = await svc.watchlist_for_rooms(chatroom_ids=[_uid()])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_group_subject_code_preserved(self) -> None:
        db = AsyncMock()
        svc = AggregationService(db)
        now = dt.datetime.now(dt.UTC)
        raw = [
            ("g:aaaaaaaa", 1, now),
            ("u:bbbbbbbb", 5, now),
        ]
        with patch.object(svc._repo, "participant_submission_counts", return_value=raw):
            result = await svc.watchlist_for_rooms(chatroom_ids=[_uid()])
        codes = [e.subject_code for e in result]
        assert "g:aaaaaaaa" in codes

    @pytest.mark.asyncio
    async def test_empty_chatroom_ids_returns_empty(self) -> None:
        db = AsyncMock()
        svc = AggregationService(db)
        result = await svc.watchlist_for_rooms(chatroom_ids=[])
        assert result == []
