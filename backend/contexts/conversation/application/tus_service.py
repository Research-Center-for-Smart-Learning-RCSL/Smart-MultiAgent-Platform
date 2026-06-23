"""TUS v1.0.0 resumable upload orchestration (R22.15.01–R22.15.07).

Scope split:

  - Protocol handling (header parsing, offsets, 409/413 translation) lives
    in this file.
  - Byte storage during upload → local disk (staging_dir) so PATCH can
    append without buffering the whole file in Redis.
  - Finalisation (MinIO put + DB row + scan enqueue) delegates to
    `AttachmentService.finalize_tus`.

The module deliberately does not depend on FastAPI — routers in
`app/api/v1/tus.py` translate method/headers into these calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Final

_log = logging.getLogger(__name__)

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.attachment_service import (
    ATTACHMENT_TTL,
    TUS_MAX_BYTES,
    AttachmentService,
)
from contexts.conversation.domain.errors import (
    AttachmentTooLarge,
    TusMetadataInvalid,
    TusOffsetMismatch,
    TusUploadNotFound,
)
from contexts.conversation.domain.models import MessageAttachment
from contexts.conversation.infrastructure.tus_store import (
    TusUpload,
    TusUploadStore,
    parse_metadata,
)
from shared_kernel.observability.metrics import TUS_UPLOAD_BYTES

TUS_VERSION: Final = "1.0.0"
TUS_MAX_CHUNK: Final = 16 * 1024 * 1024  # R22.15.04
TUS_EXTENSIONS: Final = "creation,termination"


def _staging_dir() -> str:
    # Configurable via env so prod can mount a shared volume; default is the
    # OS temp dir which works for single-node compose.
    override = os.environ.get("SMAP_TUS_STAGING_DIR")
    base = override or os.path.join(tempfile.gettempdir(), "smap-tus")
    os.makedirs(base, exist_ok=True)
    return base


def _staging_path(upload_id: uuid.UUID) -> str:
    return os.path.join(_staging_dir(), f"{upload_id}.part")


@dataclass(frozen=True, slots=True)
class TusCreateResult:
    upload_id: uuid.UUID
    location: str  # response Location header value


@dataclass(frozen=True, slots=True)
class TusHeadResult:
    upload_length: int
    upload_offset: int
    metadata_raw: str


@dataclass(frozen=True, slots=True)
class TusPatchResult:
    new_offset: int
    completed: bool
    attachment: MessageAttachment | None
    rag_document_id: uuid.UUID | None = None


class TusService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._store = TusUploadStore()
        self._attachments = AttachmentService(db)

    # ---- create -----------------------------------------------------------

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        upload_length: int,
        metadata_raw: str,
    ) -> TusCreateResult:
        if upload_length > TUS_MAX_BYTES:
            raise AttachmentTooLarge(
                f"declared length {upload_length} exceeds 1 GiB tus cap",
            )
        if upload_length < 0:
            raise TusMetadataInvalid("Upload-Length must be non-negative")
        try:
            meta = parse_metadata(metadata_raw)
        except ValueError as exc:
            raise TusMetadataInvalid(str(exc)) from exc

        purpose = meta.get("purpose", "")
        if purpose not in ("chat_attachment", "rag_source"):
            raise TusMetadataInvalid(
                "Upload-Metadata.purpose must be chat_attachment or rag_source",
            )
        filename = meta.get("filename", "")
        mime = meta.get("mime", "")
        if not filename or not mime:
            raise TusMetadataInvalid(
                "Upload-Metadata must include filename and mime",
            )
        project_id = _must_uuid(meta, "project_id")
        chatroom_id = _optional_uuid(meta, "chatroom_id")
        rag_config_id = _optional_uuid(meta, "rag_config_id")
        if purpose == "chat_attachment" and chatroom_id is None:
            raise TusMetadataInvalid(
                "chat_attachment uploads require chatroom_id",
            )
        if purpose == "rag_source" and rag_config_id is None:
            raise TusMetadataInvalid(
                "rag_source uploads require rag_config_id",
            )

        upload_id = uuid.uuid4()
        path = _staging_path(upload_id)
        # Pre-allocate the file so subsequent PATCHes can open for append.
        with open(path, "ab") as fh:  # — local staging
            fh.truncate(0)

        upload = TusUpload(
            upload_id=upload_id,
            user_id=user_id,
            upload_length=upload_length,
            upload_offset=0,
            purpose=purpose,
            project_id=project_id,
            chatroom_id=chatroom_id,
            rag_config_id=rag_config_id,
            filename=filename,
            mime=mime,
            staging_path=path,
            metadata_raw=metadata_raw,
        )
        await self._store.create(upload)
        return TusCreateResult(
            upload_id=upload_id,
            location=f"/api/tus/{upload_id}",
        )

    # ---- head -------------------------------------------------------------

    async def head(
        self,
        *,
        upload_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TusHeadResult:
        upload = await self._store.get(upload_id)
        if upload is None:
            raise TusUploadNotFound(str(upload_id))
        if upload.user_id != user_id:
            # Keep the 404 shape — don't leak that someone else's upload
            # exists at this id.
            raise TusUploadNotFound(str(upload_id))
        return TusHeadResult(
            upload_length=upload.upload_length,
            upload_offset=upload.upload_offset,
            metadata_raw=upload.metadata_raw,
        )

    # ---- patch ------------------------------------------------------------

    async def patch(
        self,
        *,
        upload_id: uuid.UUID,
        user_id: uuid.UUID,
        offset: int,
        chunk: bytes,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> TusPatchResult:
        upload = await self._store.get(upload_id)
        if upload is None or upload.user_id != user_id:
            raise TusUploadNotFound(str(upload_id))
        if offset != upload.upload_offset:
            raise TusOffsetMismatch(
                f"expected offset {upload.upload_offset}, got {offset}",
            )
        if len(chunk) > TUS_MAX_CHUNK:
            raise AttachmentTooLarge(
                f"PATCH chunk {len(chunk)} bytes exceeds 16 MB cap",
            )
        new_offset = offset + len(chunk)
        if new_offset > upload.upload_length:
            raise TusOffsetMismatch(
                f"chunk would overflow declared length {upload.upload_length}",
            )

        # CAS first — claim the offset atomically before touching the file so a
        # concurrent PATCH with the same offset cannot append stale bytes.
        if not await self._store.update_offset(upload_id, offset, new_offset):
            raise TusOffsetMismatch("concurrent upload detected")

        def _append() -> None:
            with open(upload.staging_path, "ab") as fh:
                fh.write(chunk)

        try:
            await asyncio.to_thread(_append)
        except OSError:
            # Disk write failed (full, permissions, …).  Roll back the Redis
            # offset so the client can retry this PATCH instead of being stuck
            # with an advanced offset and a short file.
            rolled_back = await self._store.update_offset(upload_id, new_offset, offset)
            if not rolled_back:
                _log.error(
                    "TUS upload %s: disk write failed AND offset rollback failed "
                    "(expected=%d, target=%d) — upload is now unrecoverable",
                    upload_id, new_offset, offset,
                )
            raise
        TUS_UPLOAD_BYTES.inc(len(chunk))

        if new_offset < upload.upload_length:
            return TusPatchResult(
                new_offset=new_offset,
                completed=False,
                attachment=None,
            )

        # Final PATCH — finalize + clean up.  Cleanup runs in `finally` so a
        # failing finalize never orphans the staging file or Redis state.
        attachment = None
        rag_document_id: uuid.UUID | None = None
        try:
            if upload.purpose == "chat_attachment":
                assert upload.chatroom_id is not None  # guaranteed by create()
                attachment = await self._attachments.finalize_tus(
                    attachment_id=upload_id,
                    project_id=upload.project_id,
                    chatroom_id=upload.chatroom_id,
                    uploader_user_id=user_id,
                    filename=upload.filename,
                    mime=upload.mime,
                    staging_path=upload.staging_path,
                    size_bytes=upload.upload_length,
                    actor_ip=actor_ip,
                    request_id=request_id,
                )
            else:
                assert upload.rag_config_id is not None
                from contexts.knowledge.interfaces.facade import KnowledgeFacade

                doc = await KnowledgeFacade(self._db).finalize_rag_upload(
                    rag_config_id=upload.rag_config_id,
                    filename=upload.filename,
                    mime=upload.mime,
                    staging_path=upload.staging_path,
                    size_bytes=upload.upload_length,
                    uploaded_by=user_id,
                    actor_ip=actor_ip,
                    request_id=request_id,
                )
                rag_document_id = doc.id
        finally:
            with contextlib.suppress(OSError):
                os.remove(upload.staging_path)
            await self._store.delete(upload_id)

        return TusPatchResult(
            new_offset=new_offset,
            completed=True,
            attachment=attachment,
            rag_document_id=rag_document_id,
        )

    # ---- terminate --------------------------------------------------------

    async def terminate(
        self,
        *,
        upload_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        upload = await self._store.get(upload_id)
        if upload is None or upload.user_id != user_id:
            # Idempotent terminate — 204 either way.
            return
        with contextlib.suppress(OSError):  # pragma: no cover
            os.remove(upload.staging_path)
        await self._store.delete(upload_id)


def _must_uuid(meta: dict[str, str], key: str) -> uuid.UUID:
    v = meta.get(key, "")
    try:
        return uuid.UUID(v)
    except (ValueError, TypeError) as exc:
        raise TusMetadataInvalid(f"Upload-Metadata.{key} must be a UUID") from exc


def _optional_uuid(meta: dict[str, str], key: str) -> uuid.UUID | None:
    v = meta.get(key)
    if not v:
        return None
    try:
        return uuid.UUID(v)
    except (ValueError, TypeError) as exc:
        raise TusMetadataInvalid(f"Upload-Metadata.{key} must be a UUID") from exc


# Shared ATTACHMENT_TTL re-exported for callers that only import this module
_ = ATTACHMENT_TTL

__all__ = [
    "TUS_EXTENSIONS",
    "TUS_MAX_BYTES",
    "TUS_MAX_CHUNK",
    "TUS_VERSION",
    "TusCreateResult",
    "TusHeadResult",
    "TusPatchResult",
    "TusService",
]
