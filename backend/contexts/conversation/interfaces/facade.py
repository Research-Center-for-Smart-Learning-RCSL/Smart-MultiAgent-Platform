"""Conversation facade — read-only surface for the web and other contexts.

Writers must go through the use-case services. Cross-context consumers (e.g.
the WS layer in F.6) read chatroom / message state through this facade so
they never import repositories or tables directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import (
    AttachmentExtractionStatus,
    AttachmentStatus,
    Chatroom,
    ChatroomGuest,
    Message,
    MessageAttachment,
    SenderType,
    Workspace,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomGuestRepository,
    ChatroomRepository,
    MessageAttachmentRepository,
    MessageRepository,
    WorkspaceRepository,
)
from shared_kernel.auth.clients import now


class ConversationFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._workspaces = WorkspaceRepository(db)
        self._rooms = ChatroomRepository(db)
        self._messages = MessageRepository(db)
        self._guests = ChatroomGuestRepository(db)
        self._room_agents = ChatroomAgentRepository(db)
        self._attachments = MessageAttachmentRepository(db)

    async def get_workspace(
        self,
        workspace_id: uuid.UUID,
    ) -> Workspace | None:
        return await self._workspaces.get(workspace_id)

    async def restore_chatroom(
        self,
        *,
        resource_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted chatroom (R8.13)."""
        from contexts.conversation.application.chatroom_service import ChatroomService

        return await ChatroomService(self._db).admin_restore(
            chatroom_id=resource_id,
            admin_user_id=admin_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def list_workspaces(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[Workspace]:
        return await self._workspaces.list_for_project(project_id)

    async def get_chatroom(
        self,
        chatroom_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Chatroom | None:
        return await self._rooms.get(chatroom_id, include_deleted=include_deleted)

    async def lock_live_chatroom_scope(self, chatroom_id: uuid.UUID) -> uuid.UUID | None:
        """Lock a live room and workspace while resolving its project scope.

        Cross-context publishers call this before room-scoped side effects and
        retain the shared transaction through publication. The lock prevents a
        concurrent soft delete from making a validated target stale.
        """
        return await self._rooms.lock_live_project_id(chatroom_id)

    async def list_chatroom_ids_for_project(
        self,
        project_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Live chatroom ids across the project's workspaces (workflow linter)."""
        return await self._rooms.list_ids_for_project(project_id)

    async def chatroom_by_guest_token(self, token: str) -> Chatroom | None:
        return await self._rooms.get_by_guest_token(token)

    async def is_chatroom_guest(
        self,
        *,
        chatroom_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> bool:
        return await self._guests.is_guest(
            chatroom_id=chatroom_id,
            user_id=user_id,
        )

    async def list_guests(self, chatroom_id: uuid.UUID) -> Sequence[ChatroomGuest]:
        return await self._guests.list(chatroom_id)

    async def distinct_user_sender_ids(self, chatroom_id: uuid.UUID, *, limit: int = 1000) -> set[uuid.UUID]:
        """Human author ids present in the room's live message history (capped)."""
        return await self._messages.distinct_user_sender_ids(chatroom_id, limit=limit)

    async def get_message(self, message_id: uuid.UUID) -> Message | None:
        return await self._messages.get(message_id)

    async def shared_room_by_agent(self, agent_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
        """target_agent_id -> a live chatroom it shares with ``agent_id`` (G.2).

        A2A broadcast uses this to grant rule 3a to the caller's room-mates: the
        shared room is the invocation context both are attached to.
        """
        return await self._room_agents.shared_room_by_agent(agent_id)

    async def is_agent_in_chatroom(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> bool:
        """Whether an agent is a member of a room (its read-ACL, Phase 2b WS3).

        The GraphRAG evidence fetcher uses this to drop excerpts sourced from
        rooms the querying agent does not participate in (AC-7).
        """
        return await self._room_agents.is_registered(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
        )

    async def list_messages(
        self,
        chatroom_id: uuid.UUID,
        *,
        limit: int = 100,
        before_id: uuid.UUID | None = None,
    ) -> list[Message]:
        """Return messages for *chatroom_id* (newest-first).

        Delegates to :pymethod:`MessageRepository.list`. The ``before_id``
        cursor maps to the repository's ``before`` parameter.
        """
        rows = await self._messages.list(
            chatroom_id=chatroom_id,
            before=before_id,
            limit=limit,
        )
        return list(rows)

    async def create_message(
        self,
        *,
        chatroom_id: uuid.UUID,
        sender_type: SenderType,
        sender_id: uuid.UUID | None,
        content_md: str,
        metadata: dict[str, object] | None = None,
    ) -> Message:
        """Insert a new message row.

        General SYSTEM/agent/user insert used by the transcript compaction store,
        the observation-release flow, and structured-activity echoes. The caller
        owns commit; ``metadata`` is stamped server-side by the caller (clients
        cannot supply message metadata)."""
        return await self._messages.create(
            chatroom_id=chatroom_id,
            sender_type=sender_type,
            sender_id=sender_id,
            content_md=content_md,
            metadata=metadata,
        )

    async def insert_system_message(
        self,
        *,
        chatroom_id: uuid.UUID,
        content_md: str,
        message_type: str,
        metadata: dict[str, object] | None = None,
    ) -> Message:
        """Insert a service-stamped SYSTEM message (``sender_id=None``).

        A thin ergonomic wrapper over :pymeth:`create_message` for cross-context
        callers (e.g. the activities echo) that namespaces a service-owned
        ``metadata["type"]``. The ``type`` is always service-stamped here, never
        forgeable by a client."""
        stamped: dict[str, object] = {**(metadata or {}), "type": message_type}
        return await self._messages.create(
            chatroom_id=chatroom_id,
            sender_type=SenderType.SYSTEM,
            sender_id=None,
            content_md=content_md,
            metadata=stamped,
        )

    # -- Code-Interpreter staging (read-only) ----------------------------------

    async def latest_user_attachments(self, chatroom_id: uuid.UUID) -> list[MessageAttachment]:
        """Active attachments on the room's most recent user message.

        This is the fallback resolver for turns with no specific triggering
        message (``silence_minutes`` wake-ups, coalesced re-enqueues) — see
        ``attachments_for_message`` for the primary, race-free resolver keyed
        on an explicit message id.
        """
        recent = await self._messages.list(chatroom_id=chatroom_id, before=None, limit=20)
        user_msg = next((m for m in recent if m.sender_type is SenderType.USER), None)
        if user_msg is None:
            return []
        attachments = await self._attachments.list_for_message(user_msg.id)
        return [a for a in attachments if a.status is AttachmentStatus.ACTIVE]

    async def attachments_for_message(self, message_id: uuid.UUID) -> list[MessageAttachment]:
        """Active attachments bound to *message_id*.

        Resolves against the exact message that triggered a turn, fixing the
        race where a fast follow-up message made ``latest_user_attachments``
        return the wrong (attachment-less) row.
        """
        attachments = await self._attachments.list_for_message(message_id)
        return [a for a in attachments if a.status is AttachmentStatus.ACTIVE]

    async def list_attachments_for_messages(
        self,
        message_ids: Sequence[uuid.UUID],
    ) -> Mapping[uuid.UUID, Sequence[MessageAttachment]]:
        """Group attachments by message id for a page of messages (one query).

        Used by the turn-history loader to batch-fetch attachments for a
        whole room window without an N+1 query.

        Unlike ``attachments_for_message``/``latest_user_attachments``, this
        does NOT filter by ``AttachmentStatus`` — it mirrors
        ``MessageService.list_attachments_for``'s "return everything for this
        page of messages" contract (display/grouping use cases may need
        quarantined/expired rows too). Callers that need only model-visible
        attachments must filter on ``AttachmentStatus.ACTIVE`` themselves, as
        ``transcript.py``'s excerpt builder already does."""
        grouped: dict[uuid.UUID, list[MessageAttachment]] = {}
        for a in await self._attachments.list_for_messages(message_ids):
            if a.message_id is not None:
                grouped.setdefault(a.message_id, []).append(a)
        return grouped

    async def read_attachments_bytes(self, attachments: Sequence[MessageAttachment]) -> list[bytes | None]:
        """Fetch several attachments' bytes from object storage concurrently.

        Object reads are independent (no shared DB session), so they run in
        parallel; ``None`` for any attachment that is not active or fails to
        read. Order matches the input."""
        import asyncio

        from shared_kernel.storage import get_minio_client

        minio = get_minio_client()

        async def _read(att: MessageAttachment) -> bytes | None:
            if att.status is not AttachmentStatus.ACTIVE:
                return None
            bucket, _, key = att.minio_path.partition("/")
            try:
                return await minio.get_object(bucket=bucket, key=key)
            except Exception:
                return None

        return list(await asyncio.gather(*[_read(a) for a in attachments]))

    # -- Retention helpers (H4) ------------------------------------------------

    async def expire_attachments(self, *, limit: int = 500) -> int:
        """Mark one batch of message-bound attachments past their TTL EXPIRED.

        Deliberately separate from `purge_old_attachments`, which *deletes*.
        The row is what R13.11 requires the client to keep so it can render
        `[attachment expired]`, so expiring and purging are different
        operations over disjoint row sets (bound versus never-bound).
        """
        from contexts.conversation.application.attachment_service import AttachmentService

        return await AttachmentService(self._db).expire_due(limit=limit)

    async def purge_old_attachments(self, *, max_age_days: int = 3) -> int:
        """Delete orphaned message_attachments older than *max_age_days*.

        Only removes attachments that were never linked to a message
        (``message_id IS NULL``) and whose ``expires_at`` has passed.
        """
        from contexts.conversation.infrastructure import tables as t

        cutoff = now() - timedelta(days=max_age_days)
        batch = (
            sa.select(t.message_attachments.c.id)
            .where(t.message_attachments.c.expires_at.is_not(None))
            .where(t.message_attachments.c.expires_at < cutoff)
            .where(t.message_attachments.c.message_id.is_(None))
            .limit(500)
        )
        result = await self._db.execute(
            sa.delete(t.message_attachments)
            .where(t.message_attachments.c.expires_at.is_not(None))
            .where(t.message_attachments.c.expires_at < cutoff)
            .where(t.message_attachments.c.message_id.is_(None))
            .where(t.message_attachments.c.id.in_(batch))
        )
        return result.rowcount or 0


__all__ = [
    "AttachmentExtractionStatus",
    "AttachmentStatus",
    "ConversationFacade",
    "Message",
    "MessageAttachment",
    "SenderType",
]
