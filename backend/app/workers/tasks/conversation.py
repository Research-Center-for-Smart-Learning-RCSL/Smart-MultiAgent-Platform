"""Arq task handlers for the conversation context.

Registered in `WorkerSettings.functions`. Each function opens its own DB
session so handlers never share transactions — this matches the shape of
other contexts' worker tasks.

H19 refactor: export serialization, ACL checks, and MinIO upload have been
extracted into ``ChatExportService``. The task is now a thin orchestrator
that calls the service and handles job state transitions.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any

from loguru import logger

from contexts.conversation.application import export_service
from contexts.conversation.application.attachment_service import (
    MAX_STORED_EXTRACTED_TEXT_CHARS,
    AttachmentService,
)
from contexts.conversation.domain.models import MessageAttachment, ScanStatus
from shared_kernel.db.session import get_sessionmaker

_file_scan_warned = False


async def _fetch_attachment_or_none(
    sm: Any, attachment_id: uuid.UUID, *, log_prefix: str
) -> MessageAttachment | None:
    """Shared first phase for attachment worker tasks: open a session, look
    up the row, and log when it's gone (deleted between enqueue and dequeue).
    Shared by ``file_scan_requested`` and ``extract_attachment_text`` so the
    two tasks don't drift independently on this boilerplate."""
    async with sm() as session, session.begin():
        attachment = await AttachmentService(session).get_by_id(attachment_id)
    if attachment is None:
        logger.warning("{}: attachment {} not found", log_prefix, attachment_id)
    return attachment


async def _fetch_object_bytes(minio_path: str) -> bytes:
    """Resolve an attachment's ``minio_path`` ("bucket/key") and fetch its bytes."""
    from shared_kernel.storage.minio_client import get_minio_client

    bucket, key = minio_path.split("/", 1)
    return await get_minio_client().get_object(bucket=bucket, key=key)


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
    global _file_scan_warned

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

    from shared_kernel.scanning import ScanError, get_scanner

    scanner = get_scanner()
    if scanner is None:
        raise RuntimeError("file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set")

    settings = get_settings()
    aid = uuid.UUID(attachment_id)
    sm = get_sessionmaker()
    attachment = await _fetch_attachment_or_none(sm, aid, log_prefix="file_scan")
    if attachment is None:
        return "not_found"

    if attachment.size_bytes > settings.security.clamav_max_scan_bytes:
        logger.warning(
            "file_scan: attachment {} skipped — {} bytes exceeds scan limit {}",
            attachment_id,
            attachment.size_bytes,
            settings.security.clamav_max_scan_bytes,
        )
        async with sm() as s2, s2.begin():
            await AttachmentService(s2).record_scan_result(
                attachment_id=aid,
                scan_status=ScanStatus.SKIPPED,
                actor_ip=None,
            )
        return "skipped:too_large"

    data = await _fetch_object_bytes(attachment.minio_path)

    try:
        result = await scanner.scan(data)
    except ScanError:
        logger.exception("file_scan: ClamAV error for attachment {}", attachment_id)
        async with sm() as s2, s2.begin():
            await AttachmentService(s2).record_scan_result(
                attachment_id=aid,
                scan_status=ScanStatus.SKIPPED,
                actor_ip=None,
            )
        raise

    scan_status = ScanStatus.CLEAN if result.clean else ScanStatus.QUARANTINED
    if not result.clean:
        logger.warning(
            "file_scan: attachment {} quarantined — threat={}",
            attachment_id,
            result.threat_name,
        )

    async with sm() as session, session.begin():
        service = AttachmentService(session)
        await service.record_scan_result(
            attachment_id=aid,
            scan_status=scan_status,
            actor_ip=None,
        )
    return scan_status.value


file_scan_requested.max_tries = 3  # type: ignore[attr-defined]


async def extract_attachment_text(ctx: dict[str, Any], attachment_id: str) -> str:
    """Best-effort text extraction for a chat attachment.

    Enqueued alongside the AV scan whenever a human uploads a chat
    attachment (F.5 / bug fix: agents lost grounding in a file's content on
    any turn after the one where it happened to be the room's latest
    message). Extraction is independent of the scan outcome — it only feeds
    the durable-text replay in later turns (transcript.py), never the live
    turn — so a parse failure, unsupported mime, or a since-deleted
    attachment all resolve to a terminal status rather than raising; later
    turns simply see no excerpt and behave exactly as they do today.
    """
    from contexts.conversation.domain.models import AttachmentExtractionStatus
    from shared_kernel.text_extraction.parsers import MIME_TO_PARSER, ParserError

    aid = uuid.UUID(attachment_id)
    sm = get_sessionmaker()
    attachment = await _fetch_attachment_or_none(sm, aid, log_prefix="extract_attachment_text")
    if attachment is None:
        return "not_found"

    mime = (attachment.mime or "").split(";", 1)[0].strip().lower()
    parser = MIME_TO_PARSER.get(mime)
    if parser is None:
        async with sm() as s2, s2.begin():
            await AttachmentService(s2).record_extraction_result(
                attachment_id=aid,
                extraction_status=AttachmentExtractionStatus.UNSUPPORTED,
                extracted_text=None,
            )
        return "unsupported"

    data = await _fetch_object_bytes(attachment.minio_path)

    try:
        text = parser(data)
    except ParserError:
        logger.opt(exception=True).warning("extract_attachment_text: parse failed for {}", attachment_id)
        async with sm() as s2, s2.begin():
            await AttachmentService(s2).record_extraction_result(
                attachment_id=aid,
                extraction_status=AttachmentExtractionStatus.FAILED,
                extracted_text=None,
            )
        return "failed"

    clipped = text[:MAX_STORED_EXTRACTED_TEXT_CHARS] if text and text.strip() else None
    # A parser that ran cleanly but found nothing (e.g. a blank .txt) is a
    # distinct outcome from "we have no parser for this mime" — EMPTY says
    # "we tried, there's nothing to replay" rather than conflating the two.
    status = AttachmentExtractionStatus.EXTRACTED if clipped else AttachmentExtractionStatus.EMPTY
    async with sm() as s3, s3.begin():
        await AttachmentService(s3).record_extraction_result(
            attachment_id=aid,
            extraction_status=status,
            extracted_text=clipped,
        )
    return status.value


extract_attachment_text.max_tries = 3  # type: ignore[attr-defined]


async def chat_export(
    ctx: dict[str, Any],
    job_id: str,
    chatroom_id: str,
    owner_user_id: str,
) -> str:
    """Write the manifest + sanitised HTML to the `exports` bucket.

    Job state transitions are managed here; the actual export build and
    upload is delegated to ``ChatExportService.build_and_upload_export``.
    """
    from contexts.conversation.application.chat_export_service import ChatExportService

    jid = uuid.UUID(job_id)
    rid = uuid.UUID(chatroom_id)
    oid = uuid.UUID(owner_user_id)

    # Defence-in-depth: don't trust the enqueued args. Verify the job state
    # in Redis matches what the API stored.
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
            enqueued = ctx.get("enqueue_time")
            if hasattr(enqueued, "isoformat"):
                exported_at = enqueued.isoformat()  # type: ignore[union-attr]
            else:
                exported_at = str(enqueued) if enqueued else None
            svc = ChatExportService(session)
            bucket, key = await svc.build_and_upload_export(
                job_id=jid,
                chatroom_id=rid,
                owner_user_id=oid,
                exported_at=exported_at,
                export_format=state.export_format,
                created_after=state.created_after,
                created_before=state.created_before,
            )
        await export_service.mark_ready(
            job_id=jid,
            bucket=bucket,
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


async def compact_chatroom(ctx: dict[str, Any], chatroom_id: str) -> str:
    """Run the forced compaction requested by POST /compact (G.10).

    The endpoint sets the one-shot ``compact:pending:{room}`` flag *and*
    enqueues this job, so compaction happens promptly even if no agent turn
    fires within the flag's 1-hour TTL. Compaction is room-level (one summary
    row in ``messages``), so the first live bound agent's config drives the
    pass — the engine consumes the flag, summarises via the agent's key group,
    and commits. If a racing turn already consumed the flag this run is a
    cheap no-op (the engine's normal mode/cap check applies).

    Moved here from orchestration.py (M19) — compaction is a conversation
    context concern.
    """
    from app.config.settings import get_settings
    from contexts.agents.application.runtime.turn_engine import TurnEngine
    from contexts.conversation.infrastructure.repositories import (
        ChatroomAgentRepository,
        ChatroomRepository,
    )
    from shared_kernel.db.session import async_session

    rid = uuid.UUID(chatroom_id)
    async with async_session() as db:
        room = await ChatroomRepository(db).get(rid)
        if room is None:
            logger.bind(room_id=chatroom_id).info("compact skipped: room gone")
            return "skipped:room_gone"
        bindings = await ChatroomAgentRepository(db).list(rid)
        if not bindings:
            logger.bind(room_id=chatroom_id).info("compact skipped: no bound agents")
            return "skipped:no_agents"
        settings = get_settings()
        engine = TurnEngine(
            db,
            qdrant_url=settings.qdrant.url,
            qdrant_api_key=settings.qdrant.api_key,
        )
        for binding in bindings:
            ok = await engine.run_compaction(agent_id=binding.agent_id, chatroom_id=rid)
            if ok:
                logger.bind(room_id=chatroom_id, agent_id=str(binding.agent_id)).info("compact pass run")
                return "completed"
        return "failed"


_ = BytesIO  # reserved for future streaming path

__all__ = ["chat_export", "compact_chatroom", "extract_attachment_text", "file_scan_requested"]
