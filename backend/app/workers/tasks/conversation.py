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
from contexts.conversation.application.attachment_service import AttachmentService
from contexts.conversation.domain.models import ScanStatus
from shared_kernel.db.session import get_sessionmaker

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
    from shared_kernel.storage.minio_client import get_minio_client

    scanner = get_scanner()
    if scanner is None:
        raise RuntimeError(
            "file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set"
        )

    aid = uuid.UUID(attachment_id)
    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        service = AttachmentService(session)
        attachment = await service._repo.get(aid)
        if attachment is None:
            logger.warning("file_scan: attachment %s not found", attachment_id)
            return "not_found"

    bucket, key = attachment.minio_path.split("/", 1)
    minio = get_minio_client()
    data = await minio.get_object(bucket=bucket, key=key)

    try:
        result = await scanner.scan(data)
    except ScanError:
        logger.exception("file_scan: ClamAV error for attachment %s", attachment_id)
        raise

    scan_status = ScanStatus.CLEAN if result.clean else ScanStatus.QUARANTINED
    if not result.clean:
        logger.warning(
            "file_scan: attachment %s quarantined — threat=%s",
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
    _ = ctx
    return scan_status.value


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

__all__ = ["chat_export", "compact_chatroom", "file_scan_requested"]
