"""Read model over submissions (Chapter §30, R30.10).

Powers the list/aggregate API, the observer context provider, and the
teacher dashboard ([R33.01]). Room-scoped reads plus project-scoped
dashboard aggregates.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import (
    ActivityAggregate,
    ActivitySubmission,
    RecentActivityRow,
    ResearchExportSubmission,
    RoundDashboardAggregate,
    RoundSubject,
    SubjectKind,
    TimeseriesBucket,
    WatchlistEntry,
)
from contexts.activities.infrastructure.repositories.submission_repo import (
    ActivitySubmissionRepository,
)

_WATCHLIST_IDLE_MINUTES = 10


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

    async def round_aggregates(
        self,
        *,
        activation_ids: Sequence[uuid.UUID],
    ) -> list[RoundDashboardAggregate]:
        return await self._repo.round_aggregates(activation_ids=activation_ids)

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

    async def list_for_research_export(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        created_after: dt.datetime | None = None,
        created_before: dt.datetime | None = None,
    ) -> list[ResearchExportSubmission]:
        return await self._repo.list_for_research_export(
            chatroom_ids=list(chatroom_ids),
            created_after=created_after,
            created_before=created_before,
        )

    async def watchlist_for_rounds(
        self,
        *,
        rounds: Sequence[tuple[uuid.UUID, uuid.UUID, dt.datetime, bool]],
        roster: Mapping[uuid.UUID, set[tuple[uuid.UUID, str]]],
        session_subjects: Mapping[uuid.UUID, set[RoundSubject]],
        now_at: dt.datetime | None = None,
    ) -> list[WatchlistEntry]:
        """Merge each current round's present roster with its valid-submission counts.

        Round tuples are ``(room_id, activation_id, started_at, is_active)``.
        The roster keys are ``(subject_id, subject_kind)`` pairs; activity sessions
        are included even if that subject has never sent a message or submission.
        """
        if not rounds:
            return []
        current = now_at or dt.datetime.now(dt.UTC)
        counts = await self._repo.round_subject_counts(
            activation_ids=[activation_id for _, activation_id, _, _ in rounds]
        )
        count_by_subject = {
            (row.activation_id, row.subject_kind.value, row.subject_id): row for row in counts
        }
        entries: list[WatchlistEntry] = []
        for room_id, activation_id, started_at, is_active in rounds:
            sessions = session_subjects.get(activation_id, set())
            participants = {(subject.subject_id, subject.subject_kind.value) for subject in sessions}
            # A group round attributes validity to groups only, so listing the
            # individual people present would flag every one of them.
            if not any(subject.subject_kind is SubjectKind.MEMBER_GROUP for subject in sessions):
                participants.update(roster.get(room_id, set()))
            for subject_id, subject_kind in participants:
                count_row = count_by_subject.get((activation_id, subject_kind, subject_id))
                valid_count = count_row.valid_count if count_row is not None else 0
                last_valid_at = count_row.last_valid_submission_at if count_row is not None else None
                if valid_count == 0:
                    reason = "no_valid_in_round"
                elif is_active and (last_valid_at or started_at) < current - dt.timedelta(
                    minutes=_WATCHLIST_IDLE_MINUTES
                ):
                    reason = "idle"
                else:
                    reason = None
                entries.append(
                    WatchlistEntry(
                        chatroom_id=room_id,
                        subject_kind=SubjectKind(subject_kind),
                        subject_id=subject_id,
                        valid_count=valid_count,
                        last_valid_submission_at=last_valid_at,
                        needs_attention=reason is not None,
                        reason=reason,
                    )
                )
        entries.sort(
            key=lambda entry: (
                not entry.needs_attention,
                str(entry.chatroom_id),
                entry.subject_kind.value,
                str(entry.subject_id),
            )
        )
        return entries[:500]


__all__ = ["AggregationService"]
