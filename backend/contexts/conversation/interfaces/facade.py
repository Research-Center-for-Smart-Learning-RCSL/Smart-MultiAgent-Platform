"""Conversation facade — read-only surface for the web and other contexts.

Writers must go through the use-case services. Cross-context consumers (e.g.
the WS layer in F.6) read chatroom / message state through this facade so
they never import repositories or tables directly.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import (
    Chatroom,
    ChatroomGuest,
    Message,
    SenderType,
    Workspace,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomGuestRepository,
    ChatroomRepository,
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

    async def get_workspace(
        self,
        workspace_id: uuid.UUID,
    ) -> Workspace | None:
        return await self._workspaces.get(workspace_id)

    async def list_workspaces(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[Workspace]:
        return await self._workspaces.list_for_project(project_id)

    async def get_chatroom(
        self,
        chatroom_id: uuid.UUID,
    ) -> Chatroom | None:
        return await self._rooms.get(chatroom_id)

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

    async def distinct_user_sender_ids(self, chatroom_id: uuid.UUID) -> set[uuid.UUID]:
        """Human author ids present in the room's live message history."""
        return await self._messages.distinct_user_sender_ids(chatroom_id)

    async def get_message(self, message_id: uuid.UUID) -> Message | None:
        return await self._messages.get(message_id)

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
        """Insert a new message row (used by the transcript compaction store)."""
        return await self._messages.create(
            chatroom_id=chatroom_id,
            sender_type=sender_type,
            sender_id=sender_id,
            content_md=content_md,
            metadata=metadata,
        )

    # -- Retention helpers (H4) ------------------------------------------------

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


__all__ = ["ConversationFacade", "Message", "SenderType"]
