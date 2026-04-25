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

# Manifest writes to MinIO are bounded so a hung S3 connection cannot pin
# an Arq worker indefinitely (R22.15 / I.4 — every worker call must be
# bounded). 60 s is comfortably above the typical 50 MiB manifest at LAN
# rates and far below the Arq job_timeout default.
_EXPORT_PUT_TIMEOUT_SECONDS = 60

from loguru import logger

from contexts.conversation.application import export_service
from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.application.attachment_service import AttachmentService
from contexts.conversation.application.retention_service import RetentionService
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


async def file_scan_requested(ctx: dict[str, Any], attachment_id: str) -> str:
    """Placeholder scan task (R22.15.07).

    "Unconfigured = no-op pass" per spec — we mark the attachment CLEAN
    by default. A real integration (ClamAV / VirusTotal) swaps this
    function body and may return QUARANTINED, which
    `AttachmentService.record_scan_result` translates into
    `status='quarantined'` + audit.
    """
    aid = uuid.UUID(attachment_id)
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            service = AttachmentService(session)
            await service.record_scan_result(
                attachment_id=aid,
                scan_status=ScanStatus.CLEAN,
                actor_ip=None,
            )
    _ = ctx
    return "clean"


async def retention_purge(ctx: dict[str, Any]) -> dict[str, int]:
    """Nightly sweep — chunked so a single run cannot hold the worker
    indefinitely. The cron is scheduled to 03:10 UTC; the chunking keeps
    each slice bounded under one minute."""
    total_messages = 0
    total_objects = 0
    sm = get_sessionmaker()
    # Loop chunks until a slice reports zero deletions.
    for _ in range(100):
        async with sm() as session:
            async with session.begin():
                service = RetentionService(session)
                report = await service.purge_once()
        if report.messages_deleted == 0:
            break
        total_messages += report.messages_deleted
        total_objects += report.attachments_objects_removed
    logger.bind(
        event="retention_purge_done",
        messages_deleted=total_messages,
        objects_removed=total_objects,
    ).info("retention purge complete")
    _ = ctx
    return {
        "messages_deleted": total_messages,
        "attachment_objects_removed": total_objects,
    }


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
    _ = timedelta  # noqa: F841 — hint for callers reading this module

    # Defence-in-depth: don't trust the enqueued args. Verify the job state
    # in Redis matches what the API stored, then re-run the room ACL for the
    # claimed owner. A malicious enqueue (bypassing the HTTP gate in
    # exports.py) would otherwise let one user export another user's room.
    state = await export_service.get(jid)
    if state is None:
        raise PermissionError(f"export job {jid} has no state record")
    if state.chatroom_id != rid or state.owner_user_id != oid:
        await export_service.mark_failed(
            job_id=jid, error="job parameters do not match stored state",
        )
        raise PermissionError(
            f"export job {jid} args mismatch stored state",
        )

    await export_service.mark_running(jid)
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            async with session.begin():
                # Re-fetch admin status from DB so legitimate admin exports of
                # rooms they aren't a member of still succeed; impersonated
                # admins never reach this path because the export endpoint is
                # in the impersonation deny-list (auth middleware).
                is_admin = await IdentityFacade(session).is_admin(oid)
                principal = Principal(
                    user_id=oid, is_admin=is_admin, email_verified=True,
                )
                access = await resolve_room_access(
                    session, principal=principal, chatroom_id=rid,
                )
                ensure_can_read(access, is_admin=is_admin)

                rooms = ChatroomRepository(session)
                messages = MessageRepository(session)
                edits = MessageEditRepository(session)
                attachments = MessageAttachmentRepository(session)

                room = await rooms.get(rid)
                rows = await messages.all_for_chatroom(rid)
                serialized = []
                for m in rows:
                    msg_edits = await edits.list_for_message(m.id)
                    msg_atts = await attachments.list_for_message(m.id)
                    serialized.append({
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
                    })

        enqueued = ctx.get("enqueue_time")
        exported_at = (
            enqueued.isoformat()
            if hasattr(enqueued, "isoformat")
            else (str(enqueued) if enqueued else None)
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
            manifest, separators=(",", ":"), default=str,
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
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"chat export MinIO put timed out after "
                f"{_EXPORT_PUT_TIMEOUT_SECONDS}s (job {jid})"
            ) from exc
        await export_service.mark_ready(
            job_id=jid, bucket=client.exports_bucket, object_key=key,
        )
    except Exception as exc:  # pragma: no cover — operator-visible
        logger.bind(event="chat_export_failed", job_id=str(jid)).exception(
            "export failed", exc_info=exc,
        )
        await export_service.mark_failed(job_id=jid, error=str(exc))
        raise
    return "ok"


_ = BytesIO  # reserved for future streaming path

__all__ = ["chat_export", "file_scan_requested", "retention_purge"]
