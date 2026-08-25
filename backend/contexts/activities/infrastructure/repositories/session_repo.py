"""Async repository for ``activity_sessions`` (Chapter §30, R30.01, R30.22, §5.4).

Encapsulates the lazy-open concurrency handling: the ``(activation_id,
subject_user_id)`` unique closes the two-concurrent-first-submissions race, and
``FOR UPDATE`` on the resolved session row serializes ``attempt_no`` assignment.

Every row written here carries an ``activation_id``; 0077 made the column
nullable only because pre-0077 rows have no round to point at, so "a live session
belongs to exactly one round" is an invariant of these writers rather than of the
schema.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

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
    t.activity_sessions.c.activation_id,
    t.activity_sessions.c.status,
    t.activity_sessions.c.created_at,
    t.activity_sessions.c.closed_at,
    t.activity_sessions.c.completed_at,
    t.activity_sessions.c.subject_member_group_id,
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
        activation_id=row.activation_id,  # type: ignore[attr-defined]
        completed_at=row.completed_at,  # type: ignore[attr-defined]
        subject_member_group_id=row.subject_member_group_id,  # type: ignore[attr-defined]
    )


class ActivitySessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, session_id: uuid.UUID) -> ActivitySession | None:
        row = (
            await self._db.execute(sa.select(*_SESSION_COLS).where(t.activity_sessions.c.id == session_id))
        ).first()
        return _row_to_session(row) if row is not None else None

    async def get_for_activation(
        self, *, activation_id: uuid.UUID, subject_user_id: uuid.UUID
    ) -> ActivitySession | None:
        """This subject's session for one round, or ``None``.

        Backed by the ``uq_activity_sessions_activation_subject`` unique (0077),
        so at most one row can match. Deliberately status-blind: a participant
        who declared themselves done, or whose round the facilitator has since
        ended, must resolve to the SAME row rather than silently acquiring a
        second one with its own ``attempt_no`` sequence.
        """
        row = (
            await self._db.execute(
                sa.select(*_SESSION_COLS).where(
                    sa.and_(
                        t.activity_sessions.c.activation_id == activation_id,
                        t.activity_sessions.c.subject_user_id == subject_user_id,
                    )
                )
            )
        ).first()
        return _row_to_session(row) if row is not None else None

    async def get_for_activation_group(
        self, *, activation_id: uuid.UUID, member_group_id: uuid.UUID
    ) -> ActivitySession | None:
        """This group's session for one round, or ``None``.

        The group-subject twin of :meth:`get_for_activation`, backed by
        ``uq_activity_sessions_activation_group`` (0081). Kept as a separate
        method rather than an optional argument on that one: the two are backed
        by different uniques, and a caller that passed both would be describing a
        session the CHECK makes unrepresentable.
        """
        row = (
            await self._db.execute(
                sa.select(*_SESSION_COLS).where(
                    sa.and_(
                        t.activity_sessions.c.activation_id == activation_id,
                        t.activity_sessions.c.subject_member_group_id == member_group_id,
                    )
                )
            )
        ).first()
        return _row_to_session(row) if row is not None else None

    async def create_open_for_group(
        self,
        *,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        member_group_id: uuid.UUID,
        activation_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Open a group's session for one round, or ``None`` if a concurrent open won.

        ``subject_user_id`` is left unset, which is what makes this a group
        session under ``ck_activity_sessions_one_subject``. The conflict target is
        the group unique; ``ON CONFLICT DO NOTHING`` without an explicit target
        covers it, and the caller re-selects the winner via
        :meth:`get_for_activation_group`.
        """
        result = await self._db.execute(
            pg_insert(t.activity_sessions)
            .values(
                activity_type_id=activity_type_id,
                chatroom_id=chatroom_id,
                subject_member_group_id=member_group_id,
                activation_id=activation_id,
                status=SessionStatus.OPEN.value,
            )
            .on_conflict_do_nothing()
            .returning(t.activity_sessions.c.id)
        )
        row = result.first()
        return row.id if row is not None else None

    async def create_open(
        self,
        *,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        subject_user_id: uuid.UUID,
        activation_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Open a session for one round, or ``None`` if a concurrent open won.

        ``ON CONFLICT DO NOTHING`` against the (activation, subject) unique makes
        the losing side of a two-concurrent-first-submissions race a no-op; the
        caller then re-selects the winner via :meth:`get_for_activation`.
        """
        result = await self._db.execute(
            pg_insert(t.activity_sessions)
            .values(
                activity_type_id=activity_type_id,
                chatroom_id=chatroom_id,
                subject_user_id=subject_user_id,
                activation_id=activation_id,
                status=SessionStatus.OPEN.value,
            )
            .on_conflict_do_nothing()
            .returning(t.activity_sessions.c.id)
        )
        row = result.first()
        return row.id if row is not None else None

    async def set_completed(self, session_id: uuid.UUID, *, completed: bool) -> bool:
        """Set or clear the subject's "I am finished" declaration.

        Guarded on the current value so a repeat call is a no-op (0 rows) and the
        caller can audit only real transitions -- the same shape as :meth:`close`.
        Never touches ``status``: whether the session can still take submissions
        is the facilitator's decision, not the participant's.
        """
        guard = (
            t.activity_sessions.c.completed_at.is_(None)
            if completed
            else t.activity_sessions.c.completed_at.is_not(None)
        )
        result = await self._db.execute(
            t.activity_sessions.update()
            .where(sa.and_(t.activity_sessions.c.id == session_id, guard))
            .values(completed_at=now() if completed else None)
        )
        return bool(rowcount(result))

    async def close_open_for_activation(self, activation_id: uuid.UUID) -> int:
        """Close every open session of one round, returning the count.

        The per-round counterpart of :meth:`close_open_for_type`: bounded by one
        activation, so it is safe on the facilitator's ordinary end-of-activity
        rather than only on a type going away. ``completed_at`` is left alone --
        the facilitator ending the round says nothing about who finished.
        """
        result = await self._db.execute(
            t.activity_sessions.update()
            .where(
                sa.and_(
                    t.activity_sessions.c.activation_id == activation_id,
                    t.activity_sessions.c.status == SessionStatus.OPEN.value,
                )
            )
            .values(status=SessionStatus.CLOSED.value, closed_at=now())
        )
        return rowcount(result)

    async def count_for_activation(self, activation_id: uuid.UUID) -> tuple[int, int]:
        """``(completed, in_progress)`` for one round, from a single query.

        Splits on ``completed_at``, not on ``status``: once the facilitator ends
        the round every session is closed, and reporting the whole class as
        finished at that moment would be a lie.

        Uses an aggregate ``FILTER`` clause, which is PostgreSQL-specific and
        therefore carries a ``db``-tier test (backend/CLAUDE.md).
        """
        row = (
            await self._db.execute(
                sa.select(
                    sa.func.count()
                    .filter(t.activity_sessions.c.completed_at.is_not(None))
                    .label("completed"),
                    sa.func.count().filter(t.activity_sessions.c.completed_at.is_(None)).label("in_progress"),
                ).where(t.activity_sessions.c.activation_id == activation_id)
            )
        ).one()
        return int(row.completed), int(row.in_progress)

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
        type deletion's cascade so no in-flight session outlives its type.

        Unbounded across rooms, which is correct only when the type is going away
        for everyone: a project-scoped delete, or a platform-scoped admin delete.
        A project opting out of a platform type must use
        :meth:`close_open_for_type_in_rooms` instead — see [R30.33].
        """
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

    async def close_open_for_type_in_rooms(
        self, activity_type_id: uuid.UUID, chatroom_ids: Sequence[uuid.UUID]
    ) -> int:
        """Close open sessions for a type, but only in the given rooms ([R30.33]).

        The opt-out counterpart of :meth:`close_open_for_type`. A platform type is
        live in every project that enabled it, so one project revoking its own
        access must not close another project's sessions — these are different
        operations and deliberately do not share a code path.

        An empty room list closes nothing rather than everything: the degenerate
        case of a project with no rooms must not widen into "all rooms", which is
        what an unguarded ``IN ()`` would invite.
        """
        rooms = list(chatroom_ids)
        if not rooms:
            return 0
        result = await self._db.execute(
            t.activity_sessions.update()
            .where(
                sa.and_(
                    t.activity_sessions.c.activity_type_id == activity_type_id,
                    t.activity_sessions.c.chatroom_id.in_(rooms),
                    t.activity_sessions.c.status == SessionStatus.OPEN.value,
                )
            )
            .values(status=SessionStatus.CLOSED.value, closed_at=now())
        )
        return rowcount(result)


__all__ = ["ActivitySessionRepository"]
