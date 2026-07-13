"""Activity-session lifecycle (Chapter §30, R30.01, §5.4).

Open is idempotent under the partial-unique: an existing open session for
(type, room, subject) is returned rather than duplicated; a lost lazy-open race
re-selects the winner. Caller owns commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.errors import SessionNotFound
from contexts.activities.domain.models import ActivitySession
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository


class ActivitySessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ActivitySessionRepository(db)

    async def open_session(
        self, *, activity_type_id: uuid.UUID, chatroom_id: uuid.UUID, subject_user_id: uuid.UUID
    ) -> ActivitySession:
        """Return the open session for (type, room, subject), opening one if none
        exists. At most one open session can exist per the partial-unique, so a
        concurrent open resolves to the same row."""
        existing = await self._repo.get_open(
            activity_type_id=activity_type_id, chatroom_id=chatroom_id, subject_user_id=subject_user_id
        )
        if existing is not None:
            return existing
        session_id = await self._repo.create_open(
            activity_type_id=activity_type_id, chatroom_id=chatroom_id, subject_user_id=subject_user_id
        )
        if session_id is not None:
            opened = await self._repo.get(session_id)
            if opened is not None:
                return opened
        # Lost the lazy-open race — re-select the winner.
        winner = await self._repo.get_open(
            activity_type_id=activity_type_id, chatroom_id=chatroom_id, subject_user_id=subject_user_id
        )
        if winner is None:  # pragma: no cover — a winner must exist post-conflict
            raise SessionNotFound("could not open or resolve a session")
        return winner

    async def close_session(self, *, session_id: uuid.UUID, chatroom_id: uuid.UUID) -> None:
        session = await self._repo.get(session_id)
        if session is None or session.chatroom_id != chatroom_id:
            raise SessionNotFound(str(session_id))
        await self._repo.close(session_id)

    async def get_session(self, session_id: uuid.UUID) -> ActivitySession | None:
        return await self._repo.get(session_id)


__all__ = ["ActivitySessionService"]
