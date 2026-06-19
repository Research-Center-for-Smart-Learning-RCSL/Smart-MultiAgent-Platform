"""Chat export builder (R13.17, F.10).

Encapsulates the export serialization, ACL checks, and MinIO upload that
were previously embedded in the ``chat_export`` worker task. The task
becomes a thin orchestrator that calls :meth:`build_and_upload_export` and
handles job state transitions.
"""

from __future__ import annotations

import asyncio
import json
import uuid
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
    ) -> tuple[str, str]:
        """Verify ACL, serialise messages, upload manifest.

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
        rows = await messages.all_for_chatroom(chatroom_id, limit=_EXPORT_MAX_MESSAGES)
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
        payload = json.dumps(
            manifest,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")

        # Release the DB connection before the (potentially slow) MinIO upload
        # so we don't hold a pool slot for up to _EXPORT_PUT_TIMEOUT_SECONDS.
        await self._db.flush()

        return await self._upload_manifest(job_id, payload)

    @staticmethod
    async def _upload_manifest(
        job_id: uuid.UUID,
        payload: bytes,
    ) -> tuple[str, str]:
        client = get_minio_client()
        key = export_key(job_id=job_id, filename="manifest.json")
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
                f"chat export MinIO put timed out after " f"{_EXPORT_PUT_TIMEOUT_SECONDS}s (job {job_id})"
            ) from exc

        return client.exports_bucket, key


__all__ = ["ChatExportService"]
