"""Retention purge (R13.15 / R13.25 / F.8).

Messages older than 5 years are hard-deleted; each row's cascade drops the
`message_edits` children. MinIO objects for attachments belonging to
purged messages are removed in the same sweep — the bucket's own 3-day
lifecycle rule would eventually get them, but we eagerly delete so
operator audit trails and storage cost match reality.

Runs as a nightly Arq cron (see `app/workers/main.py`). Chunked so a
large backlog cannot monopolise a worker slot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.infrastructure import tables as t
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.storage import MinioClient, get_minio_client

RETENTION = timedelta(days=5 * 365 + 1)        # 5 yrs, incl. leap day
PURGE_CHUNK = 500


@dataclass(frozen=True, slots=True)
class PurgeReport:
    messages_deleted: int
    attachments_objects_removed: int
    oldest_kept_at: object                       # datetime | None


class RetentionService:
    def __init__(
        self, db: AsyncSession, *, minio: MinioClient | None = None,
    ) -> None:
        self._db = db
        self._minio = minio or get_minio_client()

    async def purge_once(self) -> PurgeReport:
        horizon = now() - RETENTION
        # Collect victim message ids + their attachment paths in one query
        # so we can bulk-delete + parallel-remove S3 objects.
        rows = (
            await self._db.execute(
                sa.select(
                    t.messages.c.id,
                    t.messages.c.chatroom_id,
                )
                .where(t.messages.c.created_at < horizon)
                .limit(PURGE_CHUNK)
            )
        ).all()
        if not rows:
            kept = (
                await self._db.execute(
                    sa.select(sa.func.min(t.messages.c.created_at))
                )
            ).scalar()
            return PurgeReport(0, 0, kept)

        victim_ids = [r.id for r in rows]
        # Attachment rows are cascade-deleted via FK, but MinIO objects are
        # not — we must pull paths BEFORE deleting the parent rows.
        att_rows = (
            await self._db.execute(
                sa.select(t.message_attachments.c.minio_path).where(
                    t.message_attachments.c.message_id.in_(victim_ids),
                )
            )
        ).all()
        removed_objects = 0
        for att in att_rows:
            bucket, _, key = att.minio_path.partition("/")
            try:
                await self._minio.remove(bucket=bucket, key=key)
                removed_objects += 1
            except Exception:  # pragma: no cover
                # Object may already be gone via bucket lifecycle. Continue;
                # the row deletion still proceeds.
                continue

        # Group victims by chatroom so the audit slug carries one summary
        # row per room rather than N per message.
        by_room: dict[uuid.UUID, list[uuid.UUID]] = {}
        for r in rows:
            by_room.setdefault(r.chatroom_id, []).append(r.id)

        await self._db.execute(
            t.messages.delete().where(t.messages.c.id.in_(victim_ids)),
        )

        for chatroom_id, mids in by_room.items():
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="message.purged_by_retention",
                    resource_type="chatroom",
                    resource_id=chatroom_id,
                    metadata={
                        "count": len(mids),
                        "oldest_kept_at": horizon.isoformat(),
                    },
                ),
            )

        return PurgeReport(
            messages_deleted=len(victim_ids),
            attachments_objects_removed=removed_objects,
            oldest_kept_at=horizon,
        )


__all__ = ["PurgeReport", "RETENTION", "RetentionService"]
