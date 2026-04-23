"""Message use-cases (§22.10, R13.15 / R13.16 / R13.21–R13.24).

Only the send / list / permalink / edit / delete surface lives here. Markdown
sanitisation (F.7), attachments binding (F.5), search (F.10), and WS fan-out
(F.6) plug in alongside without changing this service's shape.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

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
from contexts.conversation.infrastructure.repositories import (
    MessageAttachmentRepository,
    MessageEditRepository,
    MessageRepository,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.realtime.pubsub import Publisher, room_channel


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
        self, message_id: uuid.UUID,
    ) -> Sequence[MessageAttachment]:
        return await self._attachments.list_for_message(message_id)

    async def list_edits(
        self, message_id: uuid.UUID,
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
        attachment_ids: list[uuid.UUID] | None = None,
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
        await Publisher(room_channel(chatroom_id)).emit(
            "message.created",
            {
                "message_id": str(msg.id),
                "sender_type": msg.sender_type.value,
                "sender_id": str(msg.sender_id) if msg.sender_id else None,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            },
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
            if (
                existing.sender_type is not SenderType.USER
                or existing.sender_id != authority.actor_user_id
            ):
                raise MessageImmutable("not the author")
            if existing.created_at is None or (
                now() - existing.created_at > SELF_EDIT_WINDOW
            ):
                raise MessageEditWindowExceeded(
                    "5-minute self-edit window exceeded",
                )

        # Preserve the prior text BEFORE overwriting (R13.21).
        await self._edits.record(
            message_id=message_id,
            old_content_md=existing.content_md,
            edited_by_user_id=authority.actor_user_id,
        )
        updated = await self._messages.update_content(
            message_id=message_id,
            expected_version=expected_version,
            new_content_md=new_content_md,
        )

        await Publisher(room_channel(existing.chatroom_id)).emit(
            "message.updated",
            {
                "message_id": str(message_id),
                "version": updated.version,
                "edited_at": updated.edited_at.isoformat()
                    if updated.edited_at else None,
                "by_moderator": (
                    moderator_path
                    and authority.actor_user_id != existing.sender_id
                ),
            },
        )
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
                        "original_sender_id": str(existing.sender_id)
                            if existing.sender_id else None,
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

        rowcount = await self._messages.hard_delete(message_id)
        if rowcount == 0:  # pragma: no cover — re-entrant race
            raise MessageNotFound(str(message_id))

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
                    "original_sender_id": str(existing.sender_id)
                        if existing.sender_id else None,
                },
                request_id=request_id,
            ),
        )
        await Publisher(room_channel(existing.chatroom_id)).emit(
            "message.deleted", {"message_id": str(message_id)},
        )


__all__ = ["EditAuthority", "MessageService", "SELF_EDIT_WINDOW"]
