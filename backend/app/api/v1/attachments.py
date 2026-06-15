"""`/api/chatrooms/{id}/attachments` (single-shot) + `/api/attachments/{id}`.

Per F.5 the single-shot path is capped at 32 MB; anything larger must go
through `/api/tus`. The download endpoint returns a short-lived MinIO
presigned URL so bytes are streamed directly from object storage to the
browser — no backend fan-out.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import (
    ensure_can_send,
    resolve_room_access,
)
from contexts.conversation.application.attachment_service import (
    SINGLE_SHOT_MAX_BYTES,
    AttachmentService,
)
from contexts.conversation.domain.errors import (
    AttachmentNotFound,
    AttachmentQuarantined,
    AttachmentTooLarge,
)
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["attachments"])
attachment_router = APIRouter(prefix="/api/attachments", tags=["attachments"])


class AttachmentOut(BaseModel):
    id: uuid.UUID
    chatroom_id: uuid.UUID | None
    message_id: uuid.UUID | None
    filename: str
    mime: str
    size_bytes: int
    status: str
    scan_status: str


class AttachmentDownloadOut(AttachmentOut):
    url: str


@chatroom_router.post(
    "/{chatroom_id}/attachments",
    status_code=201,
)
async def create_single_shot(
    chatroom_id: uuid.UUID = Path(...),
    file: UploadFile = File(...),
    mime: str | None = Form(default=None),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AttachmentOut:
    # ACL: must be able to send in the room (same gate as creating a message).
    access = await resolve_room_access(
        db,
        principal=principal,
        chatroom_id=chatroom_id,
    )
    ensure_can_send(access, is_admin=principal.is_admin)

    # Enforce 32 MB cap BEFORE pulling the whole blob into memory — read up
    # to 32 MiB + 1 byte and reject if we overflow.
    body = await file.read(SINGLE_SHOT_MAX_BYTES + 1)
    if len(body) > SINGLE_SHOT_MAX_BYTES:
        raise AttachmentTooLarge(
            "single-shot attachments must be ≤ 32 MB — use /api/tus above",
        )

    service = AttachmentService(db)
    attachment = await service.ingest_single_shot(
        project_id=access.project_id,
        chatroom_id=chatroom_id,
        uploader_user_id=principal.user_id,
        filename=file.filename or "unnamed",
        mime=mime or file.content_type or "application/octet-stream",
        data=body,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return to_attachment_out(attachment)


@attachment_router.get("/{attachment_id}")
async def read_attachment(
    attachment_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> AttachmentDownloadOut:
    service = AttachmentService(db)
    row = await service._repo.get(attachment_id)  # — intended
    if row is None:
        raise AttachmentNotFound(str(attachment_id))
    if row.status.value == "quarantined":
        raise AttachmentQuarantined(str(attachment_id))
    # ACL: attachment is readable iff the caller can read the chatroom it
    # belongs to. The chatroom_id is stamped at upload time.
    if row.chatroom_id is None:
        # Orphaned attachment (no room) — only admin sees it.
        if not principal.is_admin:
            raise HTTPException(status_code=403, detail="forbidden")
    else:
        access = await resolve_room_access(
            db,
            principal=principal,
            chatroom_id=row.chatroom_id,
        )
        from contexts.conversation.application.access import ensure_can_read

        ensure_can_read(access, is_admin=principal.is_admin)

    ptr = await service.get_for_download(attachment_id=attachment_id)
    base = to_attachment_out(ptr.attachment).model_dump()
    return AttachmentDownloadOut(url=ptr.url, **base)


def to_attachment_out(m: object) -> AttachmentOut:
    """Map a `MessageAttachment` domain row to the wire `AttachmentOut`.

    Shared converter — also used by the messages router to embed a message's
    attachments, so the two responses never diverge."""
    return AttachmentOut(
        id=m.id,  # type: ignore[attr-defined]
        chatroom_id=m.chatroom_id,  # type: ignore[attr-defined]
        message_id=m.message_id,  # type: ignore[attr-defined]
        filename=m.filename,  # type: ignore[attr-defined]
        mime=m.mime,  # type: ignore[attr-defined]
        size_bytes=m.size_bytes,  # type: ignore[attr-defined]
        status=m.status.value,  # type: ignore[attr-defined]
        scan_status=m.scan_status.value,  # type: ignore[attr-defined]
    )


_ = ConversationFacade  # retained for future cross-context reads

__all__ = ["AttachmentOut", "attachment_router", "chatroom_router", "to_attachment_out"]
