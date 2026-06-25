"""Chat export builder (R13.17, F.10).

Encapsulates the export serialization, ACL checks, and MinIO upload that
were previously embedded in the ``chat_export`` worker task. The task
becomes a thin orchestrator that calls :meth:`build_and_upload_export` and
handles job state transitions.
"""

from __future__ import annotations

import asyncio
import html as _html
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomRepository,
    MessageAttachmentRepository,
    MessageEditRepository,
    MessageRepository,
)
from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth.permissions import Principal
from shared_kernel.markdown import render_safe_html
from shared_kernel.storage import export_key, get_minio_client

# Cap messages per export to bound memory; 50 000 messages at ~2 KB each ~ 100 MB.
_EXPORT_MAX_MESSAGES = 50_000
# Manifest writes to MinIO are bounded so a hung S3 connection cannot pin
# an Arq worker indefinitely (R22.15 / I.4).
_EXPORT_PUT_TIMEOUT_SECONDS = 60


class ChatExportService:
    """Builds a chat export manifest and uploads it to MinIO."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def build_and_upload_export(
        self,
        *,
        job_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        exported_at: str | None = None,
        export_format: str = "markdown",
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> tuple[str, str]:
        """Verify ACL, serialise messages, render and upload the archive.

        *export_format* selects the rendered artifact: ``json`` (the raw
        manifest), ``markdown`` (a readable transcript), or ``pdf`` (the
        sanitised HTML rendered via WeasyPrint). *created_after* /
        *created_before* bound the message window for date-ranged exports.

        Returns ``(bucket, object_key)`` on success.
        Raises ``PermissionError`` if the owner lacks read access.
        """
        # Re-fetch admin status from DB so legitimate admin exports of
        # rooms they aren't a member of still succeed.
        is_admin = await IdentityFacade(self._db).is_admin(owner_user_id)
        principal = Principal(
            user_id=owner_user_id,
            is_admin=is_admin,
            email_verified=True,
        )
        access = await resolve_room_access(
            self._db,
            principal=principal,
            chatroom_id=chatroom_id,
        )
        ensure_can_read(access, is_admin=is_admin)

        rooms = ChatroomRepository(self._db)
        messages = MessageRepository(self._db)
        edits = MessageEditRepository(self._db)
        attachments = MessageAttachmentRepository(self._db)

        room = await rooms.get(chatroom_id)
        rows = await messages.all_for_chatroom(
            chatroom_id,
            limit=_EXPORT_MAX_MESSAGES,
            created_after=created_after,
            created_before=created_before,
        )
        serialized: list[dict[str, Any]] = []
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

        manifest: dict[str, Any] = {
            "schema_version": 1,
            "chatroom": {
                "id": str(chatroom_id),
                "name": room.name if room else None,
            },
            "exported_at": exported_at,
            "messages": serialized,
        }

        # Release the DB connection before the (potentially slow) render +
        # MinIO upload so we don't hold a pool slot for up to
        # _EXPORT_PUT_TIMEOUT_SECONDS.
        await self._db.flush()

        return await self._render_and_upload(job_id, manifest, export_format)

    @classmethod
    async def _render_and_upload(
        cls,
        job_id: uuid.UUID,
        manifest: dict[str, Any],
        export_format: str,
    ) -> tuple[str, str]:
        """Render the manifest into the requested artifact and upload it."""
        if export_format == "json":
            payload = json.dumps(manifest, separators=(",", ":"), default=str).encode("utf-8")
            return await cls._upload(job_id, payload, "manifest.json", "application/json")
        if export_format == "pdf":
            html = cls._build_pdf_html(manifest)
            # WeasyPrint is CPU-bound and blocking; render off the event loop.
            payload = await asyncio.to_thread(cls._html_to_pdf, html)
            return await cls._upload(job_id, payload, "export.pdf", "application/pdf")
        # default: markdown transcript
        text = cls._build_markdown(manifest)
        return await cls._upload(job_id, text.encode("utf-8"), "export.md", "text/markdown")

    @staticmethod
    def _sender_label(message: dict[str, Any]) -> str:
        label = str(message.get("sender_type") or "")
        sender_id = message.get("sender_id")
        if sender_id:
            label += f" ({str(sender_id)[:8]})"
        if message.get("created_at"):
            label += f" — {message['created_at']}"
        if message.get("edited_at"):
            label += " (edited)"
        return label

    @classmethod
    def _build_markdown(cls, manifest: dict[str, Any]) -> str:
        room = manifest.get("chatroom", {})
        lines: list[str] = [f"# {room.get('name') or room.get('id') or ''}", ""]
        if manifest.get("exported_at"):
            lines += [f"_Exported {manifest['exported_at']}_", ""]
        for m in manifest.get("messages", []):
            lines += [f"## {cls._sender_label(m)}", "", m.get("content_md") or ""]
            attachments = m.get("attachments") or []
            if attachments:
                names = ", ".join(a["filename"] for a in attachments)
                lines += ["", f"_Attachments: {names}_"]
            lines += ["", "---", ""]
        return "\n".join(lines)

    @classmethod
    def _build_pdf_html(cls, manifest: dict[str, Any]) -> str:
        room = manifest.get("chatroom", {})
        title = _html.escape(str(room.get("name") or room.get("id") or ""))
        parts: list[str] = [
            '<!DOCTYPE html><html><head><meta charset="utf-8"><style>',
            "body{font-family:sans-serif;font-size:12px;color:#1f2328;}",
            "h1{font-size:20px;}",
            ".meta{color:#6b7280;font-size:11px;}",
            ".msg{margin:12px 0;padding:8px 12px;border:1px solid #e5e7eb;border-radius:6px;}",
            ".msg--agent{border-left:3px solid #2563eb;background:#f8fafc;}",
            ".msg--system{border:none;color:#6b7280;font-style:italic;text-align:center;}",
            ".msg-head{font-weight:600;font-size:11px;margin-bottom:4px;}",
            "pre{background:#f8fafc;padding:8px;border-radius:4px;}",
            "table{border-collapse:collapse;}td,th{border:1px solid #e5e7eb;padding:4px;}",
            f"</style></head><body><h1>{title}</h1>",
        ]
        if manifest.get("exported_at"):
            parts.append(f'<p class="meta">Exported {_html.escape(str(manifest["exported_at"]))}</p>')
        for m in manifest.get("messages", []):
            sender_type = _html.escape(str(m.get("sender_type") or ""))
            # content_html is already sanitised (render_safe_html / bleach), so
            # embedding it carries the same XSS contract as the chat UI.
            body = m.get("content_html") or ""
            parts.append(f'<div class="msg msg--{sender_type}">')
            parts.append(f'<div class="msg-head">{_html.escape(cls._sender_label(m))}</div>')
            parts.append(f'<div class="msg-body">{body}</div>')
            attachments = m.get("attachments") or []
            if attachments:
                names = _html.escape(", ".join(a["filename"] for a in attachments))
                parts.append(f'<div class="meta">Attachments: {names}</div>')
            parts.append("</div>")
        parts.append("</body></html>")
        return "".join(parts)

    @staticmethod
    def _html_to_pdf(html: str) -> bytes:
        # Lazy import: WeasyPrint pulls in cairo/pango via cffi at import time,
        # so it is only loaded on the PDF path (and stubbed in tests).
        from weasyprint import HTML

        return bytes(HTML(string=html).write_pdf())

    @staticmethod
    async def _upload(
        job_id: uuid.UUID,
        payload: bytes,
        filename: str,
        content_type: str,
    ) -> tuple[str, str]:
        client = get_minio_client()
        key = export_key(job_id=job_id, filename=filename)
        try:
            await asyncio.wait_for(
                client.put_object(
                    bucket=client.exports_bucket,
                    key=key,
                    data=payload,
                    content_type=content_type,
                ),
                timeout=_EXPORT_PUT_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"chat export MinIO put timed out after " f"{_EXPORT_PUT_TIMEOUT_SECONDS}s (job {job_id})"
            ) from exc

        return client.exports_bucket, key


__all__ = ["ChatExportService"]
