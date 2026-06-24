"""Notification persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.notification.domain.models import Notification, NotificationKind
from contexts.notification.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_notification(row: Any) -> Notification:
    return Notification(
        id=row.id,
        user_id=row.user_id,
        kind=NotificationKind(row.kind),
        title=row.title,
        body=row.body,
        metadata=row.metadata or {},
        read_at=row.read_at,
        created_at=row.created_at,
    )


class NotificationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        kind: NotificationKind,
        title: str,
        body: str | None = None,
        metadata: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> tuple[Notification, bool]:
        """Insert a notification.

        Returns ``(notification, created)``. NOTIF-DEDUP: when ``dedup_key`` is
        set and a row with the same ``(user_id, dedup_key)`` already exists,
        nothing is inserted (``INSERT ... ON CONFLICT DO NOTHING``) and the
        existing row is returned with ``created=False`` — concurrent duplicate
        sends collide on the partial unique index instead of racing a
        non-atomic SELECT-then-INSERT check.
        """
        values = {
            "user_id": user_id,
            "kind": kind.value,
            "title": title,
            "body": body,
            "metadata": metadata or {},
            "dedup_key": dedup_key,
        }
        if dedup_key is None:
            row = (
                await self._db.execute(t.notifications.insert().values(**values).returning(t.notifications))
            ).one()
            return _row_to_notification(row), True

        stmt = (
            pg_insert(t.notifications)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["user_id", "dedup_key"],
                index_where=t.notifications.c.dedup_key.isnot(None),
            )
            .returning(t.notifications)
        )
        # `on_conflict_do_nothing` + RETURNING yields a row only when the insert
        # actually happened; a conflict suppresses the insert and returns None.
        # Use a distinct optional-typed name so the None branch stays reachable.
        inserted = (await self._db.execute(stmt)).one_or_none()
        if inserted is not None:
            return _row_to_notification(inserted), True
        # Conflict — a duplicate already exists; return the row that won.
        existing = (
            await self._db.execute(
                t.notifications.select().where(
                    sa.and_(
                        t.notifications.c.user_id == user_id,
                        t.notifications.c.dedup_key == dedup_key,
                    )
                )
            )
        ).one()
        return _row_to_notification(existing), False

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        cursor: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[Notification]:
        """List a user's notifications newest-first, keyset-paginated.

        DATA-PAGINATION: the feed is ordered by ``(created_at, id)`` and the
        cursor pages on that same composite key. ``id`` is a random v4 UUID
        uncorrelated with time, so the old ``WHERE id < cursor`` filtered an
        arbitrary subset of a ``created_at``-ordered result — silently dropping
        and duplicating rows. ``cursor`` stays a plain row id; its ``created_at``
        is resolved here so the API/cursor contract is unchanged.
        """
        q = (
            t.notifications.select()
            .where(t.notifications.c.user_id == user_id)
            .order_by(
                t.notifications.c.created_at.desc(),
                t.notifications.c.id.desc(),
            )
            .limit(limit)
        )
        if cursor is not None:
            cursor_created_at = (
                await self._db.execute(
                    sa.select(t.notifications.c.created_at).where(t.notifications.c.id == cursor)
                )
            ).scalar_one_or_none()
            if cursor_created_at is not None:
                # Composite keyset expanded to OR/AND so every comparison is
                # column-vs-value (bind types inferred from the column). Mirrors
                # MessageRepository.list — the codebase's reference keyset.
                q = q.where(
                    sa.or_(
                        t.notifications.c.created_at < cursor_created_at,
                        sa.and_(
                            t.notifications.c.created_at == cursor_created_at,
                            t.notifications.c.id < cursor,
                        ),
                    )
                )
        rows = (await self._db.execute(q)).all()
        return [_row_to_notification(r) for r in rows]

    async def mark_read(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> int:
        result = await self._db.execute(
            t.notifications.update()
            .where(
                sa.and_(
                    t.notifications.c.user_id == user_id,
                    t.notifications.c.id.in_(ids),
                    t.notifications.c.read_at.is_(None),
                )
            )
            .values(read_at=now())
        )
        return result.rowcount

    async def unread_count(self, user_id: uuid.UUID) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count())
            .select_from(t.notifications)
            .where(
                sa.and_(
                    t.notifications.c.user_id == user_id,
                    t.notifications.c.read_at.is_(None),
                )
            )
        )
        return result.scalar_one()

    async def purge_old_read(self, *, cutoff: datetime, batch_size: int = 1000) -> int:
        batch = (
            sa.select(t.notifications.c.id)
            .where(
                sa.and_(
                    t.notifications.c.read_at.isnot(None),
                    t.notifications.c.created_at < cutoff,
                )
            )
            .limit(batch_size)
        )
        result = await self._db.execute(
            sa.delete(t.notifications).where(t.notifications.c.id.in_(batch))
        )
        return result.rowcount or 0


__all__ = ["NotificationRepository"]
