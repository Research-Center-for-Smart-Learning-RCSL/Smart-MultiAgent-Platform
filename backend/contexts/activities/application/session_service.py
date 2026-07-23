"""Activity-session lifecycle (Chapter §30, R30.01, §5.4).

Open is idempotent under the partial-unique: an existing open session for
(type, room, subject) is returned rather than duplicated; a lost lazy-open race
re-selects the winner. Caller owns commit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.errors import ActivityTypeNotFound, SessionNotFound
from contexts.activities.domain.models import ActivitySession
from contexts.activities.infrastructure.repositories.session_repo import ActivitySessionRepository
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from shared_kernel import audit


def _ensure_subject_is_caller(subject_user_id: uuid.UUID, caller_user_id: uuid.UUID | None) -> None:
    """A session is a per-subject resource, so a caller may only act on their own
    subject. ``caller_user_id`` is ``None`` for the platform-admin arm (no subject
    constraint). A mismatch collapses into ``SessionNotFound`` (Q-1): the same 404
    the room/existence checks already return, which also declines to confirm the
    resource to a non-subject."""
    if caller_user_id is not None and subject_user_id != caller_user_id:
        raise SessionNotFound(str(subject_user_id))


class ActivitySessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ActivitySessionRepository(db)
        self._type_repo = ActivityTypeRepository(db)

    async def open_session(
        self,
        *,
        project_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        caller_user_id: uuid.UUID | None,
    ) -> ActivitySession:
        """Return the open session for (type, room, subject), opening one if none
        exists. At most one open session can exist per the partial-unique, so a
        concurrent open resolves to the same row. ``caller_user_id`` is ``None`` for
        the admin arm; otherwise it must equal ``subject_user_id``."""
        # Tenant isolation (mirrors SubmissionService.submit): the type must live
        # in the room's project. Missing or cross-project -> NotFound, so a room
        # member can never open a session against another tenant's type. Ordered
        # before the subject check so a cross-tenant probe still gets 404 on the
        # type rather than a subject-mismatch answer that confirms it exists.
        activity_type = await self._type_repo.get(activity_type_id)
        if activity_type is None or activity_type.project_id != project_id:
            raise ActivityTypeNotFound(str(activity_type_id))
        _ensure_subject_is_caller(subject_user_id, caller_user_id)

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

    async def close_session(
        self,
        *,
        session_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        subject_user_id: uuid.UUID | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Close a session. ``subject_user_id`` is the caller's subject constraint
        (``None`` for the admin arm); a session belonging to a different subject
        returns the same ``SessionNotFound`` (404) as a missing/wrong-room session
        (Q-1). A real close (not a double-close no-op) is audited in-transaction."""
        session = await self._repo.get(session_id)
        if (
            session is None
            or session.chatroom_id != chatroom_id
            or (subject_user_id is not None and session.subject_user_id != subject_user_id)
        ):
            raise SessionNotFound(str(session_id))
        if await self._repo.close(session_id):
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="activity.session_closed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="activity_session",
                    resource_id=session_id,
                    metadata={
                        "session_id": str(session_id),
                        "chatroom_id": str(chatroom_id),
                        "subject_user_id": str(session.subject_user_id),
                    },
                    request_id=request_id,
                ),
            )

    async def get_session(self, session_id: uuid.UUID) -> ActivitySession | None:
        return await self._repo.get(session_id)


__all__ = ["ActivitySessionService"]
