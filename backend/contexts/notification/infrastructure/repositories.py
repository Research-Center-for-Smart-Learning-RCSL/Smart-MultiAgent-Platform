"""Notification persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
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
        metadata: dict | None = None,
    ) -> Notification:
        row = (
            await self._db.execute(
                t.notifications.insert()
                .values(
                    user_id=user_id,
                    kind=kind.value,
                    title=title,
                    body=body,
                    metadata=metadata or {},
                )
                .returning(t.notifications)
            )
        ).one()
        return _row_to_notification(row)

    async def find_recent(
        self,
        user_id: uuid.UUID,
        kind: NotificationKind,
        title: str,
        *,
        window_seconds: int = 60,
    ) -> Notification | None:
        cutoff = now() - timedelta(seconds=window_seconds)
        row = (
            await self._db.execute(
                t.notifications.select()
                .where(
                    sa.and_(
                        t.notifications.c.user_id == user_id,
                        t.notifications.c.kind == kind.value,
                        t.notifications.c.title == title,
                        t.notifications.c.created_at >= cutoff,
                    )
                )
                .order_by(t.notifications.c.created_at.desc())
                .limit(1)
            )
        ).one_or_none()
        return _row_to_notification(row) if row else None

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        cursor: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[Notification]:
        q = (
            t.notifications.select()
            .where(t.notifications.c.user_id == user_id)
            .order_by(t.notifications.c.created_at.desc())
            .limit(limit)
        )
        if cursor is not None:
            q = q.where(t.notifications.c.id < cursor)
        rows = (await self._db.execute(q)).all()
        return [_row_to_notification(r) for r in rows]

    async def mark_read(
        self, user_id: uuid.UUID, ids: list[uuid.UUID]
    ) -> int:
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
        return result.rowcount  # type: ignore[return-value]

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


__all__ = ["NotificationRepository"]
