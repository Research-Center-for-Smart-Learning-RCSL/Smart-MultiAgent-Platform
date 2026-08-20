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
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import visible_room_ids
from contexts.conversation.domain.models import (
    ActivityControlGrant,
    AttachmentExtractionStatus,
    AttachmentStatus,
    Chatroom,
    ChatroomAgentRole,
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
from shared_kernel.auth.permissions import Principal

# Ceiling on the candidate rooms one visibility-filtered listing will read.
#
# The four access flags are evaluated in exactly one place — `_satisfies_room_flags`,
# in Python — so the listings pull candidates and filter in memory rather than
# keeping a second copy of the rule in SQL. That trade is only defensible while the
# candidate set is bounded, and the bound has to be loud: a listing that silently
# stops reads as "that is all there is", which for a confidentiality filter is the
# worst possible failure. See the dossier's Decision 2 and FU-3.
_MAX_LISTING_CANDIDATES = 2000


def _warn_if_truncated(truncated: bool, *, scope: str, scope_id: uuid.UUID | None) -> None:
    if not truncated:
        return
    logger.bind(
        event="room_listing_truncated",
        scope=scope,
        scope_id=str(scope_id) if scope_id else None,
        limit=_MAX_LISTING_CANDIDATES,
    ).warning(
        "room listing hit the candidate ceiling; rooms beyond it were not considered "
        "for visibility and are absent from the response"
    )


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

    # ---- visibility-filtered listings (R13.32) ---------------------------

    async def visible_rooms_in_workspace(
        self,
        *,
        principal: Principal,
        workspace_id: uuid.UUID,
    ) -> list[Chatroom]:
        """Live rooms in this workspace the caller may read, in listing order."""
        candidates, truncated = await self._rooms.list_candidates(
            workspace_ids=[workspace_id],
            limit=_MAX_LISTING_CANDIDATES,
        )
        _warn_if_truncated(truncated, scope="workspace", scope_id=workspace_id)
        visible = await self._visible_ids(principal, candidates)
        return [room for _, room in candidates if room.id in visible]

    async def workspace_ids_with_visible_room(
        self,
        *,
        principal: Principal,
        workspace_ids: Sequence[uuid.UUID],
    ) -> set[uuid.UUID]:
        """Of `workspace_ids`, those holding at least one room the caller may read."""
        candidates, truncated = await self._rooms.list_candidates(
            workspace_ids=list(workspace_ids),
            limit=_MAX_LISTING_CANDIDATES,
        )
        _warn_if_truncated(truncated, scope="workspace-set", scope_id=None)
        visible = await self._visible_ids(principal, candidates)
        return {room.workspace_id for _, room in candidates if room.id in visible}

    async def project_ids_with_visible_room(
        self,
        *,
        principal: Principal,
        project_ids: Sequence[uuid.UUID],
    ) -> set[uuid.UUID]:
        """Of `project_ids`, those holding at least one room the caller may read.

        Callers pass only the projects whose visibility is actually in question —
        a project the caller is a member of is visible on that basis alone and
        must not be routed through here, or a member with no readable room would
        lose sight of their own project.
        """
        candidates, truncated = await self._rooms.list_candidates(
            project_ids=list(project_ids),
            limit=_MAX_LISTING_CANDIDATES,
        )
        _warn_if_truncated(truncated, scope="project-set", scope_id=None)
        visible = await self._visible_ids(principal, candidates)
        return {project_id for project_id, room in candidates if room.id in visible}

    async def _visible_ids(
        self,
        principal: Principal,
        candidates: Sequence[tuple[uuid.UUID, Chatroom]],
    ) -> set[uuid.UUID]:
        return await visible_room_ids(self._db, principal=principal, rooms=candidates)

    async def get_chatroom(
        self,
        chatroom_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Chatroom | None:
        return await self._rooms.get(chatroom_id, include_deleted=include_deleted)

    async def get_chatrooms(self, chatroom_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Chatroom]:
        """Batch-resolve live rooms by id, keyed by id, for N+1-free name lookups.

        The cross-context counterpart to ``TenancyFacade.get_projects``: a caller in
        another context names rooms through this instead of joining ``chatrooms``.
        """
        return await self._rooms.get_many(chatroom_ids)

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

    async def project_id_for_chatroom(self, chatroom_id: uuid.UUID) -> uuid.UUID | None:
        """The project a live room belongs to, or ``None`` if the chain is broken.

        Rooms carry only a ``workspace_id``, so "which tenant is this" is two reads
        everywhere it is needed. Exposed here because a cross-context caller — the
        agent runtime, resolving a delegated activity grant — needs the room's
        project to run the reachability gate against, and must not walk the tables
        itself.

        ``None`` rather than raising: every caller of this treats an unresolvable
        room as "no authority", and an exception would have to be caught and turned
        back into exactly that.
        """
        room = await self._rooms.get(chatroom_id)
        if room is None:
            return None
        workspace = await self._workspaces.get(room.workspace_id)
        return workspace.project_id if workspace is not None else None

    async def agent_role_in_chatroom(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> ChatroomAgentRole | None:
        """One agent's binding role in a room, or ``None`` if it is not bound.

        Exposed because the role is a **disclosure rule**, not just a routing one:
        an ``observer`` binding is withheld from every non-creator ([R28.10]), so
        any surface that might name an agent to a room has to be able to ask. The
        activities read model uses it to decide whether a delegated round may name
        its initiator.
        """
        return await self._room_agents.role_of(chatroom_id=chatroom_id, agent_id=agent_id)

    async def activity_control_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> ActivityControlGrant | None:
        """The room's live delegation of activity control to this agent ([R30.37]).

        On the facade rather than left to the caller's own repository because the
        turn engine is in another context: the agents runtime asks this question once
        per room turn and must not reach into ``conversation.infrastructure`` to do
        it (the existing `role_of` call site that does is FU-1 of this feature's
        dossier, deliberately not widened here).

        ``None`` means "no authority", for every reason — unbound, revoked, or
        unattributable. Callers fail closed on it, and on any exception.
        """
        return await self._room_agents.activity_control_grant(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
        )

    async def set_agent_activity_grant(
        self,
        *,
        chatroom_id: uuid.UUID,
        agent_id: uuid.UUID,
        granted: bool,
        activity_type_ids: Sequence[uuid.UUID],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Write the grant ([R30.37]); ``False`` when the agent is not bound here.

        The caller owns commit, and owns having validated every id in
        ``activity_type_ids`` against the room's project first — this context cannot
        resolve an activity type ([R30.05]).
        """
        from contexts.conversation.application.chatroom_service import ChatroomService

        return await ChatroomService(self._db).set_agent_activity_grant(
            chatroom_id=chatroom_id,
            agent_id=agent_id,
            granted=granted,
            activity_type_ids=activity_type_ids,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
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

    async def note_room_activity(self, *, chatroom_id: uuid.UUID) -> None:
        """Tell the wake-up system this room is being worked in ([R15.02]).

        Exposed on the facade so a cross-context caller -- the activities submit
        route -- can re-arm the silence clock without importing this context's
        application layer, which the route rule in ``backend/CLAUDE.md`` forbids.

        Narrower than a message: it re-arms the silence timer only. A submission
        does not count toward ``every_n_messages`` and does not reset the autostop
        cap; see :func:`~contexts.conversation.application.triggers.evaluate_room_activity`
        for why counting it would be worse than the defect it fixes.
        """
        from contexts.conversation.application.triggers import evaluate_room_activity

        await evaluate_room_activity(self._db, chatroom_id=chatroom_id)

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

    async def list_attachments_after(
        self,
        *,
        after_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[MessageAttachment]:
        """One id-ordered page of every attachment row, for a full-table walk.

        Exists for the read-only size reconciliation command, which has to
        examine every row and so has no predicate any other method offers.

        **Unscoped by construction: this crosses every org, project and room.**
        It is safe only for operator-run maintenance with no request context.
        Never call it from a route -- an API surface built on it would return
        other tenants' attachments. Anything user-facing must go through a
        method that filters on the caller's chatroom or message.
        """
        return await self._attachments.list_after(after_id=after_id, limit=limit)

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
    "ActivityControlGrant",
    "AttachmentExtractionStatus",
    "AttachmentStatus",
    "ConversationFacade",
    "Message",
    "MessageAttachment",
    "SenderType",
]
