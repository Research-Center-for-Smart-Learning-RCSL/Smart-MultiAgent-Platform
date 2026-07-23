"""Async repository for ``activity_sessions`` (Chapter §30, R30.01, §5.4).

Encapsulates the lazy-open concurrency handling: a partial-unique index closes
the two-concurrent-first-submissions race, and ``FOR UPDATE`` on the resolved
session row serializes ``attempt_no`` assignment.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import ActivitySession, SessionStatus
from contexts.activities.infrastructure import tables as t
from shared_kernel.auth.clients import now
from shared_kernel.db.rowcount import rowcount

_SESSION_COLS = (
    t.activity_sessions.c.id,
    t.activity_sessions.c.activity_type_id,
    t.activity_sessions.c.chatroom_id,
    t.activity_sessions.c.subject_user_id,
    t.activity_sessions.c.status,
    t.activity_sessions.c.created_at,
    t.activity_sessions.c.closed_at,
)


def _row_to_session(row: object) -> ActivitySession:
    return ActivitySession(
        id=row.id,  # type: ignore[attr-defined]
        activity_type_id=row.activity_type_id,  # type: ignore[attr-defined]
        chatroom_id=row.chatroom_id,  # type: ignore[attr-defined]
        subject_user_id=row.subject_user_id,  # type: ignore[attr-defined]
        status=SessionStatus(row.status),  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        closed_at=row.closed_at,  # type: ignore[attr-defined]
    )


class ActivitySessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, session_id: uuid.UUID) -> ActivitySession | None:
        row = (
            await self._db.execute(sa.select(*_SESSION_COLS).where(t.activity_sessions.c.id == session_id))
        ).first()
        return _row_to_session(row) if row is not None else None

    async def get_open(
        self, *, activity_type_id: uuid.UUID, chatroom_id: uuid.UUID, subject_user_id: uuid.UUID
    ) -> ActivitySession | None:
        """The single open session for (type, room, subject), or ``None``.

        Backed by the ``uq_activity_sessions_open`` partial-unique, so at most
        one row can match.
        """
        row = (
            await self._db.execute(
                sa.select(*_SESSION_COLS).where(
                    sa.and_(
                        t.activity_sessions.c.activity_type_id == activity_type_id,
                        t.activity_sessions.c.chatroom_id == chatroom_id,
                        t.activity_sessions.c.subject_user_id == subject_user_id,
                        t.activity_sessions.c.status == SessionStatus.OPEN.value,
                    )
                )
            )
        ).first()
        return _row_to_session(row) if row is not None else None

    async def create_open(
        self, *, activity_type_id: uuid.UUID, chatroom_id: uuid.UUID, subject_user_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Open a session, or return ``None`` if a concurrent open already won.

        ``ON CONFLICT DO NOTHING`` against the partial-unique makes the losing
        side of a two-concurrent-first-submissions race a no-op; the caller then
        re-selects the winning open session via :meth:`get_open`.
        """
        result = await self._db.execute(
            pg_insert(t.activity_sessions)
            .values(
                activity_type_id=activity_type_id,
                chatroom_id=chatroom_id,
                subject_user_id=subject_user_id,
                status=SessionStatus.OPEN.value,
            )
            .on_conflict_do_nothing()
            .returning(t.activity_sessions.c.id)
        )
        row = result.first()
        return row.id if row is not None else None

    async def lock_for_update(self, session_id: uuid.UUID) -> ActivitySession | None:
        """Load a session under ``SELECT ... FOR UPDATE`` to serialize
        ``attempt_no`` assignment for concurrent submissions to it."""
        row = (
            await self._db.execute(
                sa.select(*_SESSION_COLS).where(t.activity_sessions.c.id == session_id).with_for_update()
            )
        ).first()
        return _row_to_session(row) if row is not None else None

    async def close(self, session_id: uuid.UUID) -> bool:
        """Close an open session; the ``status='open'`` guard makes a
        double-close a no-op (0 rows)."""
        result = await self._db.execute(
            t.activity_sessions.update()
            .where(
                sa.and_(
                    t.activity_sessions.c.id == session_id,
                    t.activity_sessions.c.status == SessionStatus.OPEN.value,
                )
            )
            .values(status=SessionStatus.CLOSED.value, closed_at=now())
        )
        return bool(rowcount(result))

    async def close_open_for_type(self, activity_type_id: uuid.UUID) -> int:
        """Close every open session for a type, returning the count. Used by
        type deletion's cascade so no in-flight session outlives its type."""
        result = await self._db.execute(
            t.activity_sessions.update()
            .where(
                sa.and_(
                    t.activity_sessions.c.activity_type_id == activity_type_id,
                    t.activity_sessions.c.status == SessionStatus.OPEN.value,
                )
            )
            .values(status=SessionStatus.CLOSED.value, closed_at=now())
        )
        return rowcount(result)


__all__ = ["ActivitySessionRepository"]
