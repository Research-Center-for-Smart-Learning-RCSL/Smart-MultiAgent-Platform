"""Chat export builder (R13.17, F.10).

Encapsulates the export serialization, ACL checks, and MinIO upload that
were previously embedded in the ``chat_export`` worker task. The task
becomes a thin orchestrator that calls :meth:`build_and_upload_export` and
handles job state transitions.

The build streams: messages are read in keyset pages and written to a work
directory, so a room has no message cap and memory stays flat. Attachment
files, rendering and upload run after the read transaction ends, so a
multi-gigabyte archive never holds a database connection. Packaging lives in
``chat_export_archive``; the design is in
docs/tasks/2026-10-11-chat-export-archive-content/spec.md §7.2.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import uuid
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.application import chat_export_archive as archive
from contexts.conversation.application.access import (
    ensure_can_read,
    export_own_user_id,
    export_sender_scope,
    resolve_room_access,
)
from contexts.conversation.application.chat_export_archive import AttachmentRef, Outcome, Packing
from contexts.conversation.application.room_guests import load_room_guest_labels
from contexts.conversation.domain.author_labels import prefer_guest_label
from contexts.conversation.domain.errors import ExportTooLargeForPdf
from contexts.conversation.domain.export_formats import EXPORT_ARTIFACTS, export_artifact
from contexts.conversation.domain.models import ExportSenderScope, Message, SenderType
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    MessageAttachmentRepository,
    MessageEditRepository,
    MessageRepository,
)
from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel import audit
from shared_kernel.auth.permissions import Principal
from shared_kernel.markdown import render_safe_html
from shared_kernel.storage import export_key, get_minio_client

# Uploads are bounded so a hung S3 connection cannot pin an Arq worker for the
# whole job timeout (R22.15 / I.4): a base allowance plus a floor rate.
_EXPORT_PUT_TIMEOUT_SECONDS = 60
_EXPORT_PUT_MIN_BYTES_PER_SECOND = 1024 * 1024


def _max_attachment_bytes() -> int:
    return get_settings().export.max_attachment_bytes


def pdf_max_messages() -> int:
    return get_settings().export.pdf_max_messages


async def count_export_messages(
    db: AsyncSession,
    chatroom_id: uuid.UUID,
    *,
    created_after: datetime | None,
    created_before: datetime | None,
    own_user_id: uuid.UUID | None,
) -> int:
    """How many messages an export of this window would hold for this caller."""
    return await MessageRepository(db).count_for_export(
        chatroom_id, created_after=created_after, created_before=created_before, own_user_id=own_user_id
    )


async def ensure_pdf_fits(
    db: AsyncSession,
    chatroom_id: uuid.UUID,
    *,
    created_after: datetime | None,
    created_before: datetime | None,
    sender_scope: ExportSenderScope,
    owner_user_id: uuid.UUID,
) -> None:
    """Refuse a PDF export whose window exceeds the cap, before any job exists (Q-4).

    Counted under the caller's own row-19 scope, since that is what the export
    would hold. Raises ``ExportTooLargeForPdf``.
    """
    count = await count_export_messages(
        db,
        chatroom_id,
        created_after=created_after,
        created_before=created_before,
        own_user_id=export_own_user_id(sender_scope, owner_user_id),
    )
    if count > pdf_max_messages():
        raise ExportTooLargeForPdf(message_count=count, limit=pdf_max_messages())


@dataclass(frozen=True, slots=True)
class _Collected:
    header: dict[str, Any]
    sender_scope: ExportSenderScope
    message_count: int
    attachments: list[AttachmentRef]


class ChatExportService:
    """Builds a chat export and uploads it to MinIO."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def build_and_upload_export(
        self,
        *,
        job_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        recorded_sender_scope: ExportSenderScope,
        exported_at: str | None = None,
        export_format: str = "markdown",
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        include_attachments: bool = False,
    ) -> tuple[str, str]:
        """Verify ACL, serialise messages, package and upload the export.

        *export_format* selects the transcript: ``json`` (the manifest),
        ``markdown`` (a readable transcript), or ``pdf`` (the sanitised HTML
        rendered via WeasyPrint). With *include_attachments* the upload is a
        ZIP of that transcript, an ``attachments/`` folder and a README naming
        what was left out. *created_after* / *created_before* bound the
        message window for date-ranged exports.

        *recorded_sender_scope* is what the API authorized at request time, read
        back from the job record. It is a *description*, never an authority: the
        row-19 scope is re-derived here from the database, and a re-derivation
        that comes out **wider** than the record fails the job rather than
        widening it. That keeps a compromised or stale job record unable to
        enlarge an export, matching the no-trust posture the ``chat_export`` task
        already applies to its enqueued arguments. A *narrower* re-derivation is
        honoured: it means the caller lost a role between enqueue and execution.

        The session must have no transaction open: reads run in one transaction
        and the audit row in another, and nothing in between holds a connection.

        Returns ``(bucket, object_key)`` on success.
        Raises ``PermissionError`` if the owner lacks read access or the job
        record describes a narrower export than the caller now qualifies for;
        ``ForbiddenInRoom`` if row 19 grants the caller no export at all;
        ``ExportTooLargeForPdf`` if a PDF window grew past its cap after the
        request was accepted.
        """
        work = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="chat-export-"))
        try:
            async with self._db.begin():
                collected = await self._collect(
                    work,
                    job_id=job_id,
                    chatroom_id=chatroom_id,
                    owner_user_id=owner_user_id,
                    recorded_sender_scope=recorded_sender_scope,
                    exported_at=exported_at,
                    export_format=export_format,
                    created_after=created_after,
                    created_before=created_before,
                )
            artifact = export_artifact(export_format, include_attachments=include_attachments)
            packing = Packing() if include_attachments else None
            output = await self._package(work, collected, export_format, packing)
            bucket, key = await self._upload(job_id, output, artifact.object_name, artifact.content_type)

            # R17 / REQUIREMENTS.md "Chat audit actions": emit only after the
            # artifact exists, so the trail never describes an export that was
            # not delivered. Records what was taken and under which scope --
            # never message content, filenames, or the download link (a bearer
            # credential for the archive).
            async with self._db.begin():
                await audit.emit(
                    self._db,
                    audit.AuditEvent(
                        action="message.exported",
                        actor_user_id=owner_user_id,
                        resource_type="chatroom",
                        resource_id=chatroom_id,
                        metadata={
                            "job_id": str(job_id),
                            "export_format": export_format,
                            "sender_scope": collected.sender_scope.value,
                            "created_after": created_after.isoformat() if created_after else None,
                            "created_before": created_before.isoformat() if created_before else None,
                            "message_count": collected.message_count,
                            "include_attachments": include_attachments,
                            "attachments_archived": packing.archived if packing else 0,
                            "attachments_omitted": packing.omitted if packing else 0,
                        },
                    ),
                )
            return bucket, key
        finally:
            await asyncio.to_thread(shutil.rmtree, work, True)

    async def _collect(
        self,
        work: Path,
        *,
        job_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        recorded_sender_scope: ExportSenderScope,
        exported_at: str | None,
        export_format: str,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> _Collected:
        """Authorise, then write every message in the window to the work directory."""
        # Re-fetch admin status from DB so legitimate admin exports of
        # rooms they aren't a member of still succeed.
        is_admin = await IdentityFacade(self._db).is_admin(owner_user_id)
        principal = Principal(user_id=owner_user_id, is_admin=is_admin, email_verified=True)
        access = await resolve_room_access(self._db, principal=principal, chatroom_id=chatroom_id)
        ensure_can_read(access, is_admin=is_admin)

        sender_scope = export_sender_scope(access, principal=principal)
        if sender_scope is ExportSenderScope.ALL and recorded_sender_scope is not ExportSenderScope.ALL:
            raise PermissionError(
                f"export job {job_id} re-derived a wider sender scope "
                f"({sender_scope.value}) than the job record authorized "
                f"({recorded_sender_scope.value})"
            )

        room = await ChatroomRepository(self._db).get(chatroom_id)
        messages = MessageRepository(self._db)
        edits = MessageEditRepository(self._db)
        attachments = MessageAttachmentRepository(self._db)
        pdf_cap = pdf_max_messages() if export_format == "pdf" else None

        count = 0
        refs: list[AttachmentRef] = []
        with (work / archive.MESSAGES_FILE).open("w", encoding="utf-8") as out:
            # Row 19 narrows in the WHERE clause, and attachments and edits are
            # fetched only for the messages it returned, so another
            # participant's content is never read.
            async for page in messages.iter_for_export(
                chatroom_id,
                created_after=created_after,
                created_before=created_before,
                own_user_id=export_own_user_id(sender_scope, owner_user_id),
            ):
                count += len(page)
                if pdf_cap is not None and count > pdf_cap:
                    raise ExportTooLargeForPdf(message_count=count, limit=pdf_cap)
                lines = await self._serialise_page(chatroom_id, page, edits, attachments, refs)
                await asyncio.to_thread(out.write, "".join(lines))

        header = {
            "chatroom": {"id": str(chatroom_id), "name": room.name if room else None},
            "exported_at": exported_at,
        }
        return _Collected(header=header, sender_scope=sender_scope, message_count=count, attachments=refs)

    async def _serialise_page(
        self,
        chatroom_id: uuid.UUID,
        page: Sequence[Message],
        edits: MessageEditRepository,
        attachments: MessageAttachmentRepository,
        refs: list[AttachmentRef],
    ) -> list[str]:
        ids = [m.id for m in page]
        edits_by: dict[uuid.UUID, list[Any]] = defaultdict(list)
        for e in await edits.list_for_messages(ids):
            edits_by[e.message_id].append(e)
        atts_by: dict[uuid.UUID, list[Any]] = defaultdict(list)
        for a in await attachments.list_for_messages(ids):
            if a.message_id is not None:
                atts_by[a.message_id].append(a)
        names = await self._sender_names(chatroom_id, page)

        lines: list[str] = []
        for m in page:
            msg_atts = atts_by[m.id]
            for a in msg_atts:
                refs.append(
                    AttachmentRef(
                        id=str(a.id),
                        message_id=str(m.id),
                        filename=a.filename,
                        mime=a.mime,
                        size_bytes=a.size_bytes,
                        status=a.status.value,
                        scan_status=a.scan_status.value,
                        minio_path=a.minio_path,
                        expires_at=a.expires_at,
                    )
                )
            record = {
                "id": str(m.id),
                "sender_type": m.sender_type.value,
                "sender_id": str(m.sender_id) if m.sender_id else None,
                "sender_name": names.get(m.sender_id) if m.sender_id else None,
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
                    for e in edits_by[m.id]
                ],
                # The storage path stays out: it is useless to a reader and
                # discloses the bucket layout (Q-2).
                "attachments": [
                    {
                        "id": str(a.id),
                        "filename": a.filename,
                        "mime": a.mime,
                        "size_bytes": a.size_bytes,
                        "status": a.status.value,
                    }
                    for a in msg_atts
                ],
            }
            lines.append(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return lines

    async def _sender_names(self, chatroom_id: uuid.UUID, rows: Sequence[Message]) -> dict[uuid.UUID, str]:
        """Sender id -> the name the room shows for it ([R13.17], [R13.33]).

        Accounts resolve through ``get_display_names``, which never returns the
        login email: the export is read by people.
        """
        ids: dict[SenderType, set[uuid.UUID]] = {kind: set() for kind in SenderType}
        for m in rows:
            if m.sender_id is not None:
                ids[m.sender_type].add(m.sender_id)
        names: dict[uuid.UUID, str] = {}
        if ids[SenderType.AGENT]:
            names.update(await AgentsFacade(self._db).agent_names(list(ids[SenderType.AGENT])))
        people = ids[SenderType.USER] | ids[SenderType.GUEST]
        if not people:
            return names
        guests = await load_room_guest_labels(self._db, chatroom_id)
        accounts = (
            await IdentityFacade(self._db).get_display_names(list(ids[SenderType.USER]))
            if ids[SenderType.USER]
            else {}
        )
        for uid in ids[SenderType.USER]:
            label = prefer_guest_label(guests.registered.get(uid), accounts.get(uid))
            if label:
                names[uid] = label
        for sid in ids[SenderType.GUEST]:
            if session_label := guests.sessions.get(sid):
                names[sid] = session_label
        return names

    @classmethod
    async def _package(
        cls,
        work: Path,
        collected: _Collected,
        export_format: str,
        packing: Packing | None,
    ) -> Path:
        """The file to upload: the transcript alone, or the ZIP around it."""
        transcript_name = EXPORT_ARTIFACTS[export_format].object_name
        transcript = work / f"transcript-{transcript_name}"
        if packing is None:
            await asyncio.to_thread(
                archive.write_transcript,
                export_format,
                transcript,
                work,
                collected.header,
                None,
                cls._html_to_pdf,
            )
            return transcript

        zip_path = work / "export.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            await cls._add_attachments(zf, work, collected.attachments, packing)
            # Rendered only now, so it says which files actually made it in.
            await asyncio.to_thread(
                archive.write_transcript,
                export_format,
                transcript,
                work,
                collected.header,
                packing,
                cls._html_to_pdf,
            )
            await asyncio.to_thread(zf.write, transcript, transcript_name)
            notes = archive.readme(transcript_name, collected.attachments, packing)
            await asyncio.to_thread(zf.writestr, archive.README_NAME, notes)
        return zip_path

    @staticmethod
    async def _add_attachments(
        zf: zipfile.ZipFile, work: Path, refs: list[AttachmentRef], packing: Packing
    ) -> None:
        """Copy attachment files into the archive in transcript order (Q-2, Q-3).

        Files are added until the next one would exceed the byte budget; every
        later file is then omitted too, so the earliest context stays whole.
        """
        client = get_minio_client()
        budget, used, exhausted = _max_attachment_bytes(), 0, False
        names = archive.EntryNames()
        staging = work / "attachment.part"
        for ref in refs:
            reason = archive.status_reason(ref)
            if reason is None and exhausted:
                reason = "size_budget"
            if reason is not None:
                packing.outcomes[ref.id] = Outcome(omitted_reason=reason)
                continue
            bucket, _, key = ref.minio_path.partition("/")
            # Only chat uploads belong in a chat export, whatever a row says.
            stat = await client.stat(bucket=bucket, key=key) if bucket == client.chat_uploads_bucket else None
            if stat is None:
                packing.outcomes[ref.id] = Outcome(omitted_reason="missing")
                continue
            if used + stat.size > budget:
                exhausted = True
                packing.outcomes[ref.id] = Outcome(omitted_reason="size_budget")
                continue
            await client.download_to_path(bucket=bucket, key=key, path=staging)
            entry = names.allocate(ref.message_id, ref.filename)
            await asyncio.to_thread(zf.write, staging, entry, archive.compression_for(ref.mime))
            await asyncio.to_thread(staging.unlink)
            used += stat.size
            packing.outcomes[ref.id] = Outcome(archive_path=entry)

    @staticmethod
    def _html_to_pdf(html: str) -> bytes:
        # Lazy import: WeasyPrint pulls in cairo/pango via cffi at import time,
        # so it is only loaded on the PDF path (and stubbed in tests).
        from weasyprint import HTML
        from weasyprint.urls import URLFetcher

        # Message markdown may carry `<img src="http://...">`, and the worker
        # sits on internal networks: fetching it would let any room member
        # make the worker request internal URLs (SSRF), and `file:` would read
        # its disk. Only inline `data:` resources render; anything else is an
        # image that does not load.
        fetcher = URLFetcher(allowed_protocols={"data"}, allow_redirects=False)
        return bytes(HTML(string=html, url_fetcher=fetcher).write_pdf())

    @staticmethod
    async def _upload(
        job_id: uuid.UUID,
        path: Path,
        filename: str,
        content_type: str,
    ) -> tuple[str, str]:
        client = get_minio_client()
        key = export_key(job_id=job_id, filename=filename)
        size = (await asyncio.to_thread(path.stat)).st_size
        timeout = _EXPORT_PUT_TIMEOUT_SECONDS + size / _EXPORT_PUT_MIN_BYTES_PER_SECOND
        try:
            await asyncio.wait_for(
                client.put_file(
                    bucket=client.exports_bucket,
                    key=key,
                    file_path=str(path),
                    content_type=content_type,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"chat export MinIO put timed out after {timeout:.0f}s (job {job_id})"
            ) from exc

        return client.exports_bucket, key


__all__ = ["ChatExportService", "count_export_messages", "ensure_pdf_fits", "pdf_max_messages"]
