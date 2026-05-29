"""Attachment use-cases (F.5).

Two surfaces meet here:

  1. Single-shot multipart upload — ≤ 32 MB (R22.15 threshold).
  2. TUS completion — the TUS service calls `finalize_tus` once the last
     PATCH has filled the staging file, and we atomically:
        - upload the object to MinIO,
        - insert one `message_attachments` row,
        - enqueue the scan task,
        - emit audit.

Both paths share `_create_row_and_enqueue_scan` so the bucket layout + audit
slugs remain identical regardless of how the bytes arrived.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import (
    AttachmentNotFound,
    AttachmentQuarantined,
    AttachmentTooLarge,
)
from contexts.conversation.domain.models import (
    AttachmentStatus,
    MessageAttachment,
    ScanStatus,
)
from contexts.conversation.infrastructure.repositories import (
    MessageAttachmentRepository,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.storage import (
    MinioClient,
    chat_upload_key,
    get_minio_client,
)

_log = logging.getLogger(__name__)

SINGLE_SHOT_MAX_BYTES = 32 * 1024 * 1024  # R22.15 switch-to-tus threshold
TUS_MAX_BYTES = 1024 * 1024 * 1024  # 1 GiB (R22.15.02)
ATTACHMENT_TTL = timedelta(days=3)  # §21.5 chat-uploads lifecycle

# MIME types we are willing to serve *inline* from the storage origin. Anything
# scriptable in a browser (text/html, image/svg+xml, …) is deliberately absent,
# so it is forced to download instead of rendering as attacker-controlled
# markup in the storage origin (SEC-M2). The uploader's declared `mime` is not
# trusted; this allowlist is the gate.
_INLINE_SAFE_MIME = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "application/pdf",
        "text/plain",
    }
)


def _safe_header_filename(name: str) -> str:
    """Strip control chars / quotes so the value can't break out of the
    Content-Disposition header (defence-in-depth; the SDK also URL-encodes it)."""
    cleaned = "".join(c for c in name if c.isprintable() and c not in '"\\\r\n')
    return cleaned[:200] or "download"


@dataclass(frozen=True, slots=True)
class AttachmentPointer:
    """What the router returns when a download is requested — a signed URL
    with a short TTL so the browser fetches directly from MinIO."""

    attachment: MessageAttachment
    url: str


class AttachmentService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        minio: MinioClient | None = None,
    ) -> None:
        self._db = db
        self._repo = MessageAttachmentRepository(db)
        self._minio = minio or get_minio_client()

    # ---- single-shot ------------------------------------------------------

    async def ingest_single_shot(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploader_user_id: uuid.UUID,
        filename: str,
        mime: str,
        data: bytes,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> MessageAttachment:
        if len(data) > SINGLE_SHOT_MAX_BYTES:
            raise AttachmentTooLarge(
                f"attachment {len(data)} bytes exceeds 32 MB single-shot cap",
            )
        attachment_id = uuid.uuid4()
        key = chat_upload_key(
            project_id=project_id,
            chatroom_id=chatroom_id,
            attachment_id=attachment_id,
            filename=filename,
        )
        await self._minio.put_object(
            bucket=self._minio.chat_uploads_bucket,
            key=key,
            data=data,
            content_type=mime,
        )
        return await self._create_row_and_audit(
            attachment_id=attachment_id,
            chatroom_id=chatroom_id,
            uploader_user_id=uploader_user_id,
            filename=filename,
            mime=mime,
            size_bytes=len(data),
            minio_path=f"{self._minio.chat_uploads_bucket}/{key}",
            actor_ip=actor_ip,
            request_id=request_id,
        )

    # ---- tus completion ---------------------------------------------------

    async def finalize_tus(
        self,
        *,
        attachment_id: uuid.UUID,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploader_user_id: uuid.UUID,
        filename: str,
        mime: str,
        staging_path: str,
        size_bytes: int,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> MessageAttachment:
        """Upload the staged file to MinIO and persist the attachment row.

        The TUS layer owns the staging-file lifecycle; we only read it. The
        caller is responsible for removing the staging file afterwards (it
        may want to keep the file around for forensics if Minio put fails).
        """
        key = chat_upload_key(
            project_id=project_id,
            chatroom_id=chatroom_id,
            attachment_id=attachment_id,
            filename=filename,
        )
        await self._minio.put_file(
            bucket=self._minio.chat_uploads_bucket,
            key=key,
            file_path=staging_path,
            content_type=mime,
        )
        return await self._create_row_and_audit(
            attachment_id=attachment_id,
            chatroom_id=chatroom_id,
            uploader_user_id=uploader_user_id,
            filename=filename,
            mime=mime,
            size_bytes=size_bytes,
            minio_path=f"{self._minio.chat_uploads_bucket}/{key}",
            actor_ip=actor_ip,
            request_id=request_id,
        )

    # ---- shared insert + audit + scan enqueue -----------------------------

    async def _create_row_and_audit(
        self,
        *,
        attachment_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploader_user_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        minio_path: str,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> MessageAttachment:
        expires = now() + ATTACHMENT_TTL
        row = await self._repo.create(
            attachment_id=attachment_id,
            chatroom_id=chatroom_id,
            uploaded_by_user_id=uploader_user_id,
            filename=filename,
            mime=mime,
            size_bytes=size_bytes,
            minio_path=minio_path,
            expires_at=expires,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="attachment.uploaded",
                actor_user_id=uploader_user_id,
                actor_ip=actor_ip,
                resource_type="attachment",
                resource_id=row.id,
                metadata={
                    "chatroom_id": str(chatroom_id),
                    "filename": filename,
                    "mime": mime,
                    "size_bytes": size_bytes,
                    "minio_path": minio_path,
                },
                request_id=request_id,
            ),
        )
        # Scan is fire-and-forget via Arq; failing to enqueue must NOT fail
        # the upload. The worker is imported lazily so the web image can
        # function without the worker module loaded.
        await _enqueue_scan(attachment_id=row.id)
        return row

    # ---- reads ------------------------------------------------------------

    async def get_for_download(
        self,
        *,
        attachment_id: uuid.UUID,
    ) -> AttachmentPointer:
        row = await self._repo.get(attachment_id)
        if row is None:
            raise AttachmentNotFound(str(attachment_id))
        if row.status is AttachmentStatus.QUARANTINED:
            raise AttachmentQuarantined(str(attachment_id))
        bucket, _, key = row.minio_path.partition("/")
        # Override the served Content-Type/Disposition rather than trusting the
        # uploader-declared `mime` stored on the object (SEC-M2). Known-safe
        # types render inline; everything scriptable downloads as a neutral
        # octet-stream so it can never execute in the storage origin.
        normalised_mime = (row.mime or "").split(";", 1)[0].strip().lower()
        filename = _safe_header_filename(row.filename)
        if normalised_mime in _INLINE_SAFE_MIME:
            content_type = normalised_mime
            disposition = f'inline; filename="{filename}"'
        else:
            content_type = "application/octet-stream"
            disposition = f'attachment; filename="{filename}"'
        url = await self._minio.presigned_get(
            bucket=bucket,
            key=key,
            expires=timedelta(minutes=15),
            response_content_type=content_type,
            response_content_disposition=disposition,
        )
        return AttachmentPointer(attachment=row, url=url)

    async def bind_to_message(
        self,
        *,
        attachment_ids: list[uuid.UUID],
        message_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploader_user_id: uuid.UUID,
    ) -> int:
        return await self._repo.bind_to_message(
            attachment_ids=attachment_ids,
            message_id=message_id,
            chatroom_id=chatroom_id,
            uploaded_by_user_id=uploader_user_id,
        )

    # ---- scan callback ----------------------------------------------------

    async def record_scan_result(
        self,
        *,
        attachment_id: uuid.UUID,
        scan_status: ScanStatus,
        actor_ip: str | None,
    ) -> None:
        await self._repo.mark_scan(
            attachment_id=attachment_id,
            scan_status=scan_status,
            scan_at=now(),
        )
        if scan_status is ScanStatus.QUARANTINED:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="attachment.quarantined",
                    actor_ip=actor_ip,
                    resource_type="attachment",
                    resource_id=attachment_id,
                    metadata={"scan_status": scan_status.value},
                ),
            )


async def _enqueue_scan(*, attachment_id: uuid.UUID) -> None:
    """Lazy-import the Arq pool so the worker module isn't required at
    web-image import time. Silently no-ops if Redis can't be reached —
    scanning is best-effort (R22.15.07 explicitly allows unconfigured)."""
    try:
        from arq.connections import RedisSettings, create_pool

        from app.config.settings import get_settings

        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis.dsn))
        try:
            await pool.enqueue_job(
                "file_scan_requested",
                str(attachment_id),
                _job_id=f"scan:{attachment_id}",
            )
        finally:
            await pool.close()
    except Exception:  # pragma: no cover — best-effort
        # The upload must not fail because of this, but we must alert so operators
        # know the file will not be scanned until a manual rescan is triggered.
        _log.warning(
            "scan enqueue failed for attachment %s; file will not be scanned automatically",
            attachment_id,
            exc_info=True,
        )


__all__ = [
    "ATTACHMENT_TTL",
    "AttachmentPointer",
    "AttachmentService",
    "SINGLE_SHOT_MAX_BYTES",
    "TUS_MAX_BYTES",
]
