"""Message attachment repository -- data access for file attachments."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import (
    AttachmentStatus,
    MessageAttachment,
    ScanStatus,
)
from contexts.conversation.infrastructure import tables as t


def _row_to_attachment(r: Any) -> MessageAttachment:
    return MessageAttachment(
        id=r.id,
        message_id=r.message_id,
        filename=r.filename,
        mime=r.mime,
        size_bytes=r.size_bytes,
        minio_path=r.minio_path,
        status=AttachmentStatus(r.status),
        scan_status=ScanStatus(r.scan_status),
        scan_at=r.scan_at,
        expires_at=r.expires_at,
        chatroom_id=getattr(r, "chatroom_id", None),
        uploaded_by_user_id=getattr(r, "uploaded_by_user_id", None),
    )


class MessageAttachmentRepository:
    """Populated by F.5 (single-shot + TUS completion). The `message_id`
    column is NULL between upload and the user's first send referencing the
    attachment, so every mutation carries `chatroom_id` + uploader so ACL
    checks can run against the attachment row directly."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        attachment_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploaded_by_user_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        minio_path: str,
        expires_at: datetime | None,
    ) -> MessageAttachment:
        row = (
            await self._db.execute(
                t.message_attachments.insert()
                .values(
                    id=attachment_id,
                    chatroom_id=chatroom_id,
                    uploaded_by_user_id=uploaded_by_user_id,
                    filename=filename,
                    mime=mime,
                    size_bytes=size_bytes,
                    minio_path=minio_path,
                    expires_at=expires_at,
                )
                .returning(t.message_attachments)
            )
        ).one()
        return _row_to_attachment(row)

    async def get(self, attachment_id: uuid.UUID) -> MessageAttachment | None:
        row = (
            await self._db.execute(
                t.message_attachments.select().where(
                    t.message_attachments.c.id == attachment_id,
                )
            )
        ).first()
        return _row_to_attachment(row) if row else None

    async def list_for_message(
        self,
        message_id: uuid.UUID,
    ) -> Sequence[MessageAttachment]:
        rows = (
            await self._db.execute(
                t.message_attachments.select().where(
                    t.message_attachments.c.message_id == message_id,
                )
            )
        ).all()
        return [_row_to_attachment(r) for r in rows]

    async def list_for_messages(
        self,
        message_ids: Sequence[uuid.UUID],
    ) -> Sequence[MessageAttachment]:
        """Batched variant of :meth:`list_for_message` -- one query for a page of
        messages, so the timeline render avoids an N+1 attachment fetch."""
        if not message_ids:
            return []
        rows = (
            await self._db.execute(
                t.message_attachments.select().where(
                    t.message_attachments.c.message_id.in_(list(message_ids)),
                )
            )
        ).all()
        return [_row_to_attachment(r) for r in rows]

    async def bind_to_message(
        self,
        *,
        attachment_ids: Sequence[uuid.UUID] | list[uuid.UUID],
        message_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploaded_by_user_id: uuid.UUID,
    ) -> int:
        """Bind unbound attachments to a message.

        The WHERE clause intentionally re-asserts `chatroom_id` and the
        uploader -- binding SOMEONE ELSE's upload, or an upload from a
        different room, must be impossible even if the caller supplies a
        valid UUID. Returns the number of rows actually updated so the
        caller can detect partial mismatches.
        """
        if not attachment_ids:
            return 0
        result = await self._db.execute(
            t.message_attachments.update()
            .where(
                sa.and_(
                    t.message_attachments.c.id.in_(list(attachment_ids)),
                    t.message_attachments.c.message_id.is_(None),
                    t.message_attachments.c.chatroom_id == chatroom_id,
                    t.message_attachments.c.uploaded_by_user_id == uploaded_by_user_id,
                    t.message_attachments.c.status == AttachmentStatus.ACTIVE.value,
                )
            )
            .values(message_id=message_id),
        )
        return result.rowcount or 0

    async def create_agent_artifact(
        self,
        *,
        attachment_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        minio_path: str,
        expires_at: datetime | None,
    ) -> MessageAttachment:
        """Insert an agent-authored artifact (chart/file produced by code_exec).

        ``uploaded_by_user_id`` is NULL (no human uploader) and the AV scan is
        skipped — the bytes originate inside the sandbox, not from a client.
        """
        row = (
            await self._db.execute(
                t.message_attachments.insert()
                .values(
                    id=attachment_id,
                    chatroom_id=chatroom_id,
                    uploaded_by_user_id=None,
                    filename=filename,
                    mime=mime,
                    size_bytes=size_bytes,
                    minio_path=minio_path,
                    scan_status=ScanStatus.SKIPPED.value,
                    expires_at=expires_at,
                )
                .returning(t.message_attachments)
            )
        ).one()
        return _row_to_attachment(row)

    async def bind_agent_artifacts(
        self,
        *,
        attachment_ids: Sequence[uuid.UUID],
        message_id: uuid.UUID,
        chatroom_id: uuid.UUID,
    ) -> int:
        """Bind agent-authored artifacts to a freshly-sent agent message.

        Mirrors :meth:`bind_to_message` but matches on ``uploaded_by_user_id IS
        NULL`` (agent authorship) rather than a human uploader.
        """
        if not attachment_ids:
            return 0
        result = await self._db.execute(
            t.message_attachments.update()
            .where(
                sa.and_(
                    t.message_attachments.c.id.in_(list(attachment_ids)),
                    t.message_attachments.c.message_id.is_(None),
                    t.message_attachments.c.chatroom_id == chatroom_id,
                    t.message_attachments.c.uploaded_by_user_id.is_(None),
                    t.message_attachments.c.status == AttachmentStatus.ACTIVE.value,
                )
            )
            .values(message_id=message_id),
        )
        return result.rowcount or 0

    async def mark_scan(
        self,
        *,
        attachment_id: uuid.UUID,
        scan_status: ScanStatus,
        scan_at: datetime,
    ) -> None:
        values: dict[str, Any] = {
            "scan_status": scan_status.value,
            "scan_at": scan_at,
        }
        if scan_status is ScanStatus.QUARANTINED:
            values["status"] = AttachmentStatus.QUARANTINED.value
        await self._db.execute(
            t.message_attachments.update()
            .where(t.message_attachments.c.id == attachment_id)
            .values(**values),
        )

    async def mark_expired(self, attachment_id: uuid.UUID) -> None:
        await self._db.execute(
            t.message_attachments.update()
            .where(t.message_attachments.c.id == attachment_id)
            .values(status=AttachmentStatus.EXPIRED.value),
        )

    async def list_expired(
        self,
        *,
        horizon: datetime,
        limit: int = 500,
    ) -> Sequence[MessageAttachment]:
        rows = (
            await self._db.execute(
                t.message_attachments.select()
                .where(
                    sa.and_(
                        t.message_attachments.c.expires_at.is_not(None),
                        t.message_attachments.c.expires_at < horizon,
                        t.message_attachments.c.status != AttachmentStatus.EXPIRED.value,
                    )
                )
                .limit(limit)
            )
        ).all()
        return [_row_to_attachment(r) for r in rows]

    async def list_for_chatroom_older_than(
        self,
        *,
        chatroom_id: uuid.UUID,
        horizon: datetime,
    ) -> Sequence[MessageAttachment]:
        """Used by the retention sweep: find attachments belonging to messages
        older than the retention horizon. Joins via message_id so orphan rows
        (message_id NULL) are left alone -- those are handled by `list_expired`
        driven by the bucket's 3-day lifecycle."""
        rows = (
            await self._db.execute(
                t.message_attachments.select()
                .select_from(
                    t.message_attachments.join(
                        t.messages,
                        t.message_attachments.c.message_id == t.messages.c.id,
                    )
                )
                .where(
                    sa.and_(
                        t.messages.c.chatroom_id == chatroom_id,
                        t.messages.c.created_at < horizon,
                    )
                )
            )
        ).all()
        return [_row_to_attachment(r) for r in rows]


__all__ = ["MessageAttachmentRepository"]
