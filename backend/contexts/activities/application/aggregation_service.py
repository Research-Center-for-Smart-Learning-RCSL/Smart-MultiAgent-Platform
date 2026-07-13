"""Read model over submissions (Chapter §30, R30.10).

Powers the list/aggregate API, the observer context provider, and (later)
dashboards. All reads are room-scoped; the aggregate is one grouped query.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import (
    ActivityAggregate,
    ActivitySubmission,
    RecentActivityRow,
)
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


__all__ = ["AggregationService"]
