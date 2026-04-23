"""Notification facade — the public surface for the web layer and other contexts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.notification.application.notification_service import NotificationService
from contexts.notification.domain.models import Notification, NotificationKind


class NotificationFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._service = NotificationService(db)

    async def send(
        self,
        *,
        user_id: uuid.UUID,
        kind: NotificationKind,
        title: str,
        body: str | None = None,
        metadata: dict | None = None,
    ) -> Notification:
        return await self._service.send(
            user_id=user_id, kind=kind, title=title, body=body, metadata=metadata,
        )

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        cursor: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[Notification]:
        return await self._service.list_for_user(user_id, cursor=cursor, limit=limit)

    async def mark_read(self, user_id: uuid.UUID, ids: list[uuid.UUID]) -> int:
        return await self._service.mark_read(user_id, ids)

    async def unread_count(self, user_id: uuid.UUID) -> int:
        return await self._service.unread_count(user_id)


__all__ = ["NotificationFacade"]
