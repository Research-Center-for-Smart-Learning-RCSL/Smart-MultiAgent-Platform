"""Notification dispatch (R18.01–R18.03).

Creates notification rows and pushes via the user's WS channel.
No email, webhook, or Slack — in-app only for v1.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.infrastructure.channels import user_channel
from contexts.notification.domain.models import Notification, NotificationKind
from contexts.notification.infrastructure.repositories import NotificationRepository
from shared_kernel.auth.clients import now
from shared_kernel.realtime.pubsub import Publisher


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
        metadata: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> Notification:
        """Create a notification and push it over the user's WS channel.

        NOTIF-DEDUP: duplicates are suppressed by a unique constraint on
        ``(user_id, dedup_key)`` rather than a check-then-act SELECT, so two
        concurrent identical sends can no longer both insert + both emit.
        Callers with a natural idempotency key (e.g. a source event id) should
        pass ``dedup_key`` so genuinely distinct notifications that merely share
        a title are not collapsed. When omitted, a coarse 60-second time bucket
        keyed on ``kind`` + ``title`` is used — preserving the old "same event
        delivered twice" suppression, now race-free. The WS push fires only when
        a row was actually inserted.
        """
        if dedup_key is None:
            # Coarse 60s bucket keyed on kind + title. The title is hashed with
            # a stable (process-independent) digest so the key length is bounded
            # for the unique index and dedup still works across worker processes.
            bucket = int(now().timestamp()) // 60
            title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:32]
            dedup_key = f"auto:{kind.value}:{title_hash}:{bucket}"

        notif, created = await self._repo.insert(
            user_id=user_id,
            kind=kind,
            title=title,
            body=body,
            metadata=metadata,
            dedup_key=dedup_key,
        )
        if not created:
            return notif

        await self._db.flush()
        pub = Publisher(user_channel(user_id))
        await pub.emit(
            "notification.created",
            {
                "id": str(notif.id),
                "kind": notif.kind.value,
                "title": notif.title,
            },
        )
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
