"""Arq task handlers for the conversation context.

Registered in `WorkerSettings.functions`. Each function opens its own DB
session so handlers never share transactions — this matches the shape of
other contexts' worker tasks.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from io import BytesIO
from typing import Any

from loguru import logger

from contexts.conversation.application import export_service
from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.application.attachment_service import AttachmentService
from contexts.conversation.domain.models import ScanStatus
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    MessageAttachmentRepository,
    MessageEditRepository,
    MessageRepository,
)
from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.markdown import render_safe_html
from shared_kernel.storage import export_key, get_minio_client

# Manifest writes to MinIO are bounded so a hung S3 connection cannot pin
# an Arq worker indefinitely (R22.15 / I.4 — every worker call must be
# bounded). 60 s is comfortably above the typical 50 MiB manifest at LAN
# rates and far below the Arq job_timeout default.
_EXPORT_PUT_TIMEOUT_SECONDS = 60
# Cap messages per export to bound memory; 50 000 messages at ~2 KB each ≈ 100 MB.
_EXPORT_MAX_MESSAGES = 50_000


_file_scan_warned = False


async def file_scan_requested(ctx: dict[str, Any], attachment_id: str) -> str:
    """AV scan task (R22.15.07).

    When ``settings.security.file_scan_enabled`` is ``False`` (the default),
    every attachment is auto-approved as CLEAN and a one-time warning is
    logged.  To enable real scanning, set ``SMAP_SEC_FILE_SCAN_ENABLED=true``
    and wire a ClamAV / VirusTotal adapter into this function body:

        1. Fetch the blob from MinIO via the attachment's ``minio_path``.
        2. Submit bytes to the AV engine.
        3. Call ``service.record_scan_result(…, scan_status=ScanStatus.QUARANTINED)``
           if the engine reports a threat; ``CLEAN`` otherwise.
    """
    global _file_scan_warned  # noqa: PLW0603

    from app.config.settings import get_settings

    if not get_settings().security.file_scan_enabled:
        if not _file_scan_warned:
            logger.warning(
                "File scanning disabled — all attachments auto-approved. "
                "Set SMAP_SEC_FILE_SCAN_ENABLED=true once an AV adapter is wired in."
            )
            _file_scan_warned = True

        aid = uuid.UUID(attachment_id)
        sm = get_sessionmaker()
        async with sm() as session, session.begin():
            service = AttachmentService(session)
            await service.record_scan_result(
                attachment_id=aid,
                scan_status=ScanStatus.CLEAN,
                actor_ip=None,
            )
        _ = ctx
        return "clean"

    # TODO(P15): Implement real scan path when file_scan_enabled is True.
    # Fetch the blob, submit to ClamAV/VirusTotal, record the result.
    raise NotImplementedError(
        "file_scan_enabled is True but no AV adapter is configured"
    )


async def chat_export(
    ctx: dict[str, Any],
    job_id: str,
    chatroom_id: str,
    owner_user_id: str,
) -> str:
    """Write the manifest + sanitised HTML to the `exports` bucket."""
    jid = uuid.UUID(job_id)
    rid = uuid.UUID(chatroom_id)
    oid = uuid.UUID(owner_user_id)
    _ = timedelta  # — hint for callers reading this module

    # Defence-in-depth: don't trust the enqueued args. Verify the job state
    # in Redis matches what the API stored, then re-run the room ACL for the
    # claimed owner. A malicious enqueue (bypassing the HTTP gate in
    # exports.py) would otherwise let one user export another user's room.
    state = await export_service.get(jid)
    if state is None:
        raise PermissionError(f"export job {jid} has no state record")
    if state.chatroom_id != rid or state.owner_user_id != oid:
        await export_service.mark_failed(
            job_id=jid,
            error="job parameters do not match stored state",
        )
        raise PermissionError(
            f"export job {jid} args mismatch stored state",
        )

    await export_service.mark_running(jid)
    try:
        sm = get_sessionmaker()
        async with sm() as session, session.begin():
            # Re-fetch admin status from DB so legitimate admin exports of
            # rooms they aren't a member of still succeed; impersonated
            # admins never reach this path because the export endpoint is
            # in the impersonation deny-list (auth middleware).
            is_admin = await IdentityFacade(session).is_admin(oid)
            principal = Principal(
                user_id=oid,
                is_admin=is_admin,
                email_verified=True,
            )
            access = await resolve_room_access(
                session,
                principal=principal,
                chatroom_id=rid,
            )
            ensure_can_read(access, is_admin=is_admin)

            rooms = ChatroomRepository(session)
            messages = MessageRepository(session)
            edits = MessageEditRepository(session)
            attachments = MessageAttachmentRepository(session)

            room = await rooms.get(rid)
            rows = await messages.all_for_chatroom(rid, limit=_EXPORT_MAX_MESSAGES)
            serialized = []
            for m in rows:
                msg_edits = await edits.list_for_message(m.id)
                msg_atts = await attachments.list_for_message(m.id)
                serialized.append(
                    {
                        "id": str(m.id),
                        "sender_type": m.sender_type.value,
                        "sender_id": str(m.sender_id) if m.sender_id else None,
                        "content_md": m.content_md,
                        "content_html": render_safe_html(m.content_md),
                        "metadata": m.metadata,
                        "version": m.version,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                        "edited_at": m.edited_at.isoformat() if m.edited_at else None,
                        "edits": [
                            {
                                "old_content_md": e.old_content_md,
                                "edited_by_user_id": str(e.edited_by_user_id),
                                "edited_at": e.edited_at.isoformat(),
                            }
                            for e in msg_edits
                        ],
                        "attachments": [
                            {
                                "id": str(a.id),
                                "filename": a.filename,
                                "mime": a.mime,
                                "size_bytes": a.size_bytes,
                                "minio_path": a.minio_path,
                                "status": a.status.value,
                            }
                            for a in msg_atts
                        ],
                    }
                )

        enqueued = ctx.get("enqueue_time")
        exported_at = (
            enqueued.isoformat() if hasattr(enqueued, "isoformat") else (str(enqueued) if enqueued else None)  # type: ignore[union-attr]
        )
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "chatroom": {
                "id": str(rid),
                "name": room.name if room else None,
            },
            "exported_at": exported_at,
            "messages": serialized,
        }
        payload = json.dumps(
            manifest,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")

        client = get_minio_client()
        key = export_key(job_id=jid, filename="manifest.json")
        try:
            await asyncio.wait_for(
                client.put_object(
                    bucket=client.exports_bucket,
                    key=key,
                    data=payload,
                    content_type="application/json",
                ),
                timeout=_EXPORT_PUT_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"chat export MinIO put timed out after " f"{_EXPORT_PUT_TIMEOUT_SECONDS}s (job {jid})"
            ) from exc
        await export_service.mark_ready(
            job_id=jid,
            bucket=client.exports_bucket,
            object_key=key,
        )
    except Exception as exc:  # pragma: no cover — operator-visible
        logger.bind(event="chat_export_failed", job_id=str(jid)).exception(
            "export failed",
            exc_info=exc,
        )
        await export_service.mark_failed(job_id=jid, error=str(exc))
        raise
    return "ok"


_ = BytesIO  # reserved for future streaming path

__all__ = ["chat_export", "file_scan_requested"]
