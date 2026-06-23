"""Message use-cases (§22.10, R13.15 / R13.16 / R13.21–R13.24).

Only the send / list / permalink / edit / delete surface lives here. Markdown
sanitisation (F.7), attachments binding (F.5), search (F.10), and WS fan-out
(F.6) plug in alongside without changing this service's shape.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import (
    MessageEditWindowExceeded,
    MessageImmutable,
    MessageNotFound,
)
from contexts.conversation.domain.models import (
    Message,
    MessageAttachment,
    MessageEdit,
    SenderType,
)
from contexts.conversation.infrastructure.channels import room_channel
from contexts.conversation.infrastructure.repositories import (
    MessageAttachmentRepository,
    MessageEditRepository,
    MessageRepository,
)
from contexts.conversation.infrastructure import tables as t
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.realtime.pubsub import Publisher
from shared_kernel.storage import get_minio_client

_log = logging.getLogger(__name__)


# R13.21 — 5-minute user self-edit window.
SELF_EDIT_WINDOW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class EditAuthority:
    """Snapshot of *who* is doing an edit. Computed by the router from the
    resolved RoomAccess and handed to the service so SoC between AuthZ and
    domain stays clean — this module never reaches into the tenancy or
    permissions layers on its own."""

    actor_user_id: uuid.UUID
    is_admin: bool
    is_moderator: bool  # project-owner or org-owner on this room's project


class MessageService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._messages = MessageRepository(db)
        self._edits = MessageEditRepository(db)
        self._attachments = MessageAttachmentRepository(db)

    # ---- queries ---------------------------------------------------------

    async def get(self, message_id: uuid.UUID) -> Message:
        msg = await self._messages.get(message_id)
        if msg is None:
            raise MessageNotFound(str(message_id))
        return msg

    async def list(
        self,
        *,
        chatroom_id: uuid.UUID,
        before: uuid.UUID | None,
        since: uuid.UUID | None,
        limit: int,
    ) -> Sequence[Message]:
        return await self._messages.list(
            chatroom_id=chatroom_id,
            before=before,
            since=since,
            limit=min(max(limit, 1), 200),
        )

    async def list_attachments(
        self,
        message_id: uuid.UUID,
    ) -> Sequence[MessageAttachment]:
        return await self._attachments.list_for_message(message_id)

    async def list_attachments_for(
        self,
        message_ids: Sequence[uuid.UUID],
    ) -> Mapping[uuid.UUID, Sequence[MessageAttachment]]:
        """Group attachments by message id for a page of messages (one query).

        Returns a Mapping/Sequence (not ``dict[..., list[...]]``): this class
        defines a ``list`` method, so a bare ``list[...]`` annotation here would
        resolve to that method under PEP 563 deferred annotations (pinned
        mypy 1.13 flags it as `valid-type`)."""
        grouped: dict[uuid.UUID, list[MessageAttachment]] = {}
        for a in await self._attachments.list_for_messages(message_ids):
            if a.message_id is not None:
                grouped.setdefault(a.message_id, []).append(a)
        return grouped

    async def search(
        self,
        *,
        chatroom_id: uuid.UUID,
        query: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[tuple[Message, float, str]]:
        """Full-text search within a chatroom (F.10). Delegates to the
        repository's ``search`` which uses ``plainto_tsquery``."""
        return await self._messages.search(
            chatroom_id=chatroom_id,
            query=query,
            limit=limit,
            offset=offset,
        )

    async def list_edits(
        self,
        message_id: uuid.UUID,
    ) -> Sequence[MessageEdit]:
        return await self._edits.list_for_message(message_id)

    # ---- commands --------------------------------------------------------

    async def send(
        self,
        *,
        chatroom_id: uuid.UUID,
        sender_user_id: uuid.UUID,
        content_md: str,
        metadata: dict[str, Any] | None = None,
        # NB: `Sequence`, not `list` — this class defines a `list()` method, and
        # under PEP 563 string annotations `list[...]` resolves to that method
        # (valid-type error) rather than the builtin generic.
        attachment_ids: Sequence[uuid.UUID] | None = None,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Message:
        """A user sending a message from a chat UI. Agents publish via a
        different surface (workers). `metadata` is optional — None is stored
        as `{}`. `attachment_ids`, if provided, are bound to the new message
        via `MessageAttachmentRepository.bind_to_message` which defends
        against cross-room / cross-user id injection."""
        msg = await self._messages.create(
            chatroom_id=chatroom_id,
            sender_type=SenderType.USER,
            sender_id=sender_user_id,
            content_md=content_md,
            metadata=metadata or {},
        )
        bound = 0
        if attachment_ids:
            bound = await self._attachments.bind_to_message(
                attachment_ids=attachment_ids,
                message_id=msg.id,
                chatroom_id=chatroom_id,
                uploaded_by_user_id=sender_user_id,
            )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="message.sent",
                actor_user_id=sender_user_id,
                actor_ip=actor_ip,
                resource_type="message",
                resource_id=msg.id,
                metadata={
                    "chatroom_id": str(chatroom_id),
                    "len": len(content_md),
                    "attachments_bound": bound,
                    "attachments_requested": len(attachment_ids or []),
                },
                request_id=request_id,
            ),
        )
        await self._db.flush()
        try:
            await Publisher(room_channel(chatroom_id)).emit(
                "message.created",
                {
                    "message_id": str(msg.id),
                    "sender_type": msg.sender_type.value,
                    "sender_id": str(msg.sender_id) if msg.sender_id else None,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                },
            )
        except Exception:
            _log.error("realtime publish failed for message.created %s", msg.id, exc_info=True)
        return msg

    async def send_agent(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        content_md: str,
        metadata: dict[str, Any] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> Message:
        """Persist an agent's reply (K.2). The turn engine's only write surface.

        Constructs ``sender_type=AGENT`` (which :meth:`send` refuses — it is
        user-only) and audits it. Unlike :meth:`send`, this does **not** publish
        ``message.created``: an agent reply has no optimistic client echo, so the
        ``message.created`` notification must fire *after* the turn engine commits
        (otherwise the client's refetch races an uncommitted row and the reply is
        invisible). The caller publishes post-commit. Agent messages are
        immutable (R13.22) — there is no edit/delete here.
        """
        meta = dict(metadata or {})
        meta.setdefault("type", "agent_reply")
        msg = await self._messages.create(
            chatroom_id=chatroom_id,
            sender_type=SenderType.AGENT,
            sender_id=agent_id,
            content_md=content_md,
            metadata=meta,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="message.sent",
                actor_user_id=None,
                actor_ip=None,
                resource_type="message",
                resource_id=msg.id,
                metadata={
                    "chatroom_id": str(chatroom_id),
                    "agent_id": str(agent_id),
                    "sender": "agent",
                    "len": len(content_md),
                },
                request_id=request_id,
            ),
        )
        return msg

    async def edit(
        self,
        *,
        message_id: uuid.UUID,
        expected_version: int,
        new_content_md: str,
        authority: EditAuthority,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Message:
        existing = await self._messages.get(message_id)
        if existing is None:
            raise MessageNotFound(str(message_id))

        moderator_path = authority.is_admin or authority.is_moderator
        if not moderator_path:
            # R13.22 — agents cannot edit their own past messages. Agents do
            # not reach this HTTP surface directly, but the guard is kept so
            # an accidental mis-authored request (sender_id aliased to an
            # agent) cannot mutate history.
            if existing.sender_type is SenderType.AGENT:
                raise MessageImmutable(
                    "agent messages are immutable (R13.22)",
                )
            # Self-edit path: only the author, and only within 5 minutes.
            if existing.sender_type is not SenderType.USER or existing.sender_id != authority.actor_user_id:
                raise MessageImmutable("not the author")
            if existing.created_at is None or (now() - existing.created_at > SELF_EDIT_WINDOW):
                raise MessageEditWindowExceeded(
                    "5-minute self-edit window exceeded",
                )

        # Validate the version lock first — only record history if the update
        # will actually succeed. VersionMismatch here leaves no orphaned rows.
        # For self-edit, also pass max_age so the DB re-checks the window with
        # its own clock — guards against a transaction delayed past 5 minutes
        # between the application-side check above and this UPDATE.
        updated = await self._messages.update_content(
            message_id=message_id,
            expected_version=expected_version,
            new_content_md=new_content_md,
            max_age=None if moderator_path else SELF_EDIT_WINDOW,
        )
        await self._edits.record(
            message_id=message_id,
            old_content_md=existing.content_md,
            edited_by_user_id=authority.actor_user_id,
        )

        await self._db.flush()
        try:
            await Publisher(room_channel(existing.chatroom_id)).emit(
                "message.updated",
                {
                    "message_id": str(message_id),
                    "version": updated.version,
                    "edited_at": updated.edited_at.isoformat() if updated.edited_at else None,
                    "by_moderator": (moderator_path and authority.actor_user_id != existing.sender_id),
                },
            )
        except Exception:
            _log.error("realtime publish failed for message.updated %s", message_id, exc_info=True)
        if moderator_path and authority.actor_user_id != existing.sender_id:
            # R13.23 — moderator-on-other-user edit gets its own audit slug.
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="message.edited_by_moderator",
                    actor_user_id=authority.actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="message",
                    resource_id=message_id,
                    metadata={
                        "chatroom_id": str(existing.chatroom_id),
                        "original_sender_id": str(existing.sender_id) if existing.sender_id else None,
                    },
                    request_id=request_id,
                ),
            )
        else:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="message.edited",
                    actor_user_id=authority.actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="message",
                    resource_id=message_id,
                    metadata={"chatroom_id": str(existing.chatroom_id)},
                    request_id=request_id,
                ),
            )
        return updated

    async def delete(
        self,
        *,
        message_id: uuid.UUID,
        authority: EditAuthority,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Hard delete (R13.16). Permissioned by:
          - own message any time (self), OR
          - Admin / Project Owner / Org Owner any in scope.
        Caller enforces those gates via the permission matrix + RoomAccess.
        """
        existing = await self._messages.get(message_id)
        if existing is None:
            raise MessageNotFound(str(message_id))

        # Pull MinIO paths BEFORE the cascade-delete drops attachment rows —
        # mirrors RetentionService.purge_once() (the FK cascade removes the DB
        # rows but leaves the blobs orphaned unless we clean them here).
        att_rows = (
            await self._db.execute(
                sa.select(t.message_attachments.c.minio_path).where(
                    t.message_attachments.c.message_id == message_id,
                )
            )
        ).all()

        rowcount = await self._messages.hard_delete(message_id)
        if rowcount == 0:  # pragma: no cover — re-entrant race
            raise MessageNotFound(str(message_id))

        if att_rows:
            minio = get_minio_client()
            for att in att_rows:
                bucket, _, key = att.minio_path.partition("/")
                try:
                    await minio.remove(bucket=bucket, key=key)
                except Exception:
                    _log.warning("MinIO cleanup failed for %s", att.minio_path, exc_info=True)

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="message.deleted",
                actor_user_id=authority.actor_user_id,
                actor_ip=actor_ip,
                resource_type="message",
                resource_id=message_id,
                metadata={
                    "chatroom_id": str(existing.chatroom_id),
                    "by_moderator": authority.is_admin or authority.is_moderator,
                    "original_sender_id": str(existing.sender_id) if existing.sender_id else None,
                },
                request_id=request_id,
            ),
        )
        await self._db.flush()
        try:
            await Publisher(room_channel(existing.chatroom_id)).emit(
                "message.deleted",
                {"message_id": str(message_id)},
            )
        except Exception:
            _log.error("realtime publish failed for message.deleted %s", message_id, exc_info=True)


__all__ = ["EditAuthority", "MessageService", "SELF_EDIT_WINDOW"]
