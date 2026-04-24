"""Notification dispatch (R18.01–R18.03).

Creates notification rows and pushes via the user's WS channel.
No email, webhook, or Slack — in-app only for v1.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.notification.domain.models import Notification, NotificationKind
from contexts.notification.infrastructure.repositories import NotificationRepository
from shared_kernel.realtime.pubsub import Publisher, user_channel


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = NotificationRepository(db)

    async def send(
        self,
        *,
        user_id: uuid.UUID,
        kind: NotificationKind,
        title: str,
        body: str | None = None,
        metadata: dict | None = None,
    ) -> Notification:
        existing = await self._repo.find_recent(user_id=user_id, kind=kind, title=title)
        if existing:
            return existing
        notif = await self._repo.insert(
            user_id=user_id, kind=kind, title=title, body=body, metadata=metadata,
        )
        pub = Publisher(user_channel(user_id))
        await pub.emit("notification.created", {
            "id": str(notif.id),
            "kind": notif.kind.value,
            "title": notif.title,
        })
        return notif

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        cursor: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[Notification]:
        return await self._repo.list_for_user(user_id, cursor=cursor, limit=limit)

    async def mark_read(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> int:
        return await self._repo.mark_read(user_id, ids)

    async def unread_count(self, user_id: uuid.UUID) -> int:
        return await self._repo.unread_count(user_id)


__all__ = ["NotificationService"]
