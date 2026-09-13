"""Read model over submissions (Chapter §30, R30.10).

Powers the list/aggregate API, the observer context provider, and the
teacher dashboard ([R33.01]). Room-scoped reads plus project-scoped
dashboard aggregates.
"""

from __future__ import annotations

import datetime as dt
import statistics
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import (
    ActivityAggregate,
    ActivitySubmission,
    RecentActivityRow,
    RoomDashboardAggregate,
    TimeseriesBucket,
    WatchlistEntry,
)
from contexts.activities.domain.subject_code import group_subject_code, subject_code
from contexts.activities.infrastructure.repositories.submission_repo import (
    ActivitySubmissionRepository,
)


class AggregationService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = ActivitySubmissionRepository(db)

    async def list_submissions(
        self,
        *,
        chatroom_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
        limit: int,
        offset: int,
    ) -> Sequence[ActivitySubmission]:
        return await self._repo.list_filtered(
            chatroom_id=chatroom_id,
            session_id=session_id,
            subject_user_id=subject_user_id,
            limit=limit,
            offset=offset,
        )

    async def aggregate(
        self,
        *,
        chatroom_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
    ) -> ActivityAggregate:
        return await self._repo.aggregate(
            chatroom_id=chatroom_id, session_id=session_id, subject_user_id=subject_user_id
        )

    async def list_recent_activity(
        self, *, chatroom_id: uuid.UUID, limit: int
    ) -> Sequence[RecentActivityRow]:
        """Bounded, most-recent-first activity for the observer context provider."""
        return await self._repo.list_recent_for_room(chatroom_id=chatroom_id, limit=limit)

    # -- Teacher dashboard ([R33.01]) ---------------------------------------- #

    async def aggregate_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
    ) -> list[RoomDashboardAggregate]:
        return await self._repo.aggregate_for_rooms(chatroom_ids=chatroom_ids)

    async def timeseries_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        since: dt.datetime,
        bucket_seconds: int,
    ) -> list[TimeseriesBucket]:
        return await self._repo.timeseries_for_rooms(
            chatroom_ids=chatroom_ids, since=since, bucket_seconds=bucket_seconds
        )

    async def watchlist_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
    ) -> list[WatchlistEntry]:
        """Participants at or below the median submission count."""
        raw = await self._repo.participant_submission_counts(chatroom_ids=chatroom_ids)
        if not raw:
            return []
        counts = [count for _, count, _ in raw]
        median = statistics.median(counts)
        return [
            WatchlistEntry(
                subject_code=code,
                submission_count=count,
                last_submission_at=last_at,
            )
            for code, count, last_at in raw
            if count <= median
        ]


__all__ = ["AggregationService"]
