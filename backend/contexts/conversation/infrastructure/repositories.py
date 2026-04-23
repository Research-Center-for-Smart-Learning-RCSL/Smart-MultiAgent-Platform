"""Conversation repositories — raw data access, no use-case orchestration."""

from __future__ import annotations

import base64
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import NameTaken, VersionMismatch
from contexts.conversation.domain.models import (
    AttachmentStatus,
    Chatroom,
    ChatroomAgent,
    ChatroomGuest,
    Message,
    MessageAttachment,
    MessageEdit,
    ScanStatus,
    SenderType,
    Workspace,
)
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now


# --------------------------------------------------------------------------- #
# Workspace
# --------------------------------------------------------------------------- #


def _row_to_workspace(row: Any) -> Workspace:
    return Workspace(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


class WorkspaceRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, *, project_id: uuid.UUID, name: str) -> Workspace:
        try:
            row = (
                await self._db.execute(
                    t.workspaces.insert()
                    .values(project_id=project_id, name=name)
                    .returning(t.workspaces)
                )
            ).one()
        except IntegrityError as exc:  # pragma: no cover — no unique index today
            raise NameTaken(name) from exc
        return _row_to_workspace(row)

    async def get(
        self, workspace_id: uuid.UUID, *, include_deleted: bool = False,
    ) -> Workspace | None:
        predicate = t.workspaces.c.id == workspace_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.workspaces.c.deleted_at.is_(None))
        row = (
            await self._db.execute(t.workspaces.select().where(predicate))
        ).first()
        return _row_to_workspace(row) if row else None

    async def list_for_project(
        self, project_id: uuid.UUID,
    ) -> Sequence[Workspace]:
        rows = (
            await self._db.execute(
                t.workspaces.select()
                .where(
                    sa.and_(
                        t.workspaces.c.project_id == project_id,
                        t.workspaces.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.workspaces.c.created_at)
            )
        ).all()
        return [_row_to_workspace(r) for r in rows]

    async def soft_delete(self, workspace_id: uuid.UUID) -> None:
        await self._db.execute(
            t.workspaces.update()
            .where(t.workspaces.c.id == workspace_id)
            .values(deleted_at=now())
        )


# --------------------------------------------------------------------------- #
# Chatroom
# --------------------------------------------------------------------------- #


def _row_to_chatroom(row: Any) -> Chatroom:
    return Chatroom(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        allow_org_members=row.allow_org_members,
        allow_project_members=row.allow_project_members,
        allow_project_owners_only=row.allow_project_owners_only,
        allow_guest_links=row.allow_guest_links,
        guest_token=row.guest_token,
        version=row.version,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _new_guest_token() -> str:
    """R13.05 — CSPRNG 32-byte base64url = 256 bits entropy."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode(
        "ascii",
    )


class ChatroomRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        allow_org_members: bool = False,
        allow_project_members: bool = True,
        allow_project_owners_only: bool = False,
        allow_guest_links: bool = False,
    ) -> Chatroom:
        token = _new_guest_token()
        row = (
            await self._db.execute(
                t.chatrooms.insert()
                .values(
                    workspace_id=workspace_id,
                    name=name,
                    allow_org_members=allow_org_members,
                    allow_project_members=allow_project_members,
                    allow_project_owners_only=allow_project_owners_only,
                    allow_guest_links=allow_guest_links,
                    guest_token=token,
                )
                .returning(t.chatrooms)
            )
        ).one()
        return _row_to_chatroom(row)

    async def get(
        self, chatroom_id: uuid.UUID, *, include_deleted: bool = False,
    ) -> Chatroom | None:
        predicate = t.chatrooms.c.id == chatroom_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.chatrooms.c.deleted_at.is_(None))
        row = (
            await self._db.execute(t.chatrooms.select().where(predicate))
        ).first()
        return _row_to_chatroom(row) if row else None

    async def get_by_guest_token(self, token: str) -> Chatroom | None:
        row = (
            await self._db.execute(
                t.chatrooms.select().where(
                    sa.and_(
                        t.chatrooms.c.guest_token == token,
                        t.chatrooms.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_chatroom(row) if row else None

    async def list_for_workspace(
        self, workspace_id: uuid.UUID,
    ) -> Sequence[Chatroom]:
        rows = (
            await self._db.execute(
                t.chatrooms.select()
                .where(
                    sa.and_(
                        t.chatrooms.c.workspace_id == workspace_id,
                        t.chatrooms.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.chatrooms.c.created_at)
            )
        ).all()
        return [_row_to_chatroom(r) for r in rows]

    async def count_active_in_workspace(self, workspace_id: uuid.UUID) -> int:
        row = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(t.chatrooms)
                .where(
                    sa.and_(
                        t.chatrooms.c.workspace_id == workspace_id,
                        t.chatrooms.c.deleted_at.is_(None),
                    )
                )
            )
        ).one()
        return int(row[0])

    async def update(
        self,
        *,
        chatroom_id: uuid.UUID,
        expected_version: int,
        values: dict[str, Any],
    ) -> Chatroom:
        stmt = (
            t.chatrooms.update()
            .where(
                sa.and_(
                    t.chatrooms.c.id == chatroom_id,
                    t.chatrooms.c.version == expected_version,
                    t.chatrooms.c.deleted_at.is_(None),
                )
            )
            .values(**values, version=t.chatrooms.c.version + 1)
            .returning(t.chatrooms)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            raise VersionMismatch(f"chatroom {chatroom_id} version mismatch or missing")
        return _row_to_chatroom(row)

    async def soft_delete(self, chatroom_id: uuid.UUID) -> None:
        await self._db.execute(
            t.chatrooms.update()
            .where(t.chatrooms.c.id == chatroom_id)
            .values(deleted_at=now())
        )


class ChatroomAgentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        stmt = pg.insert(t.chatroom_agents).values(
            chatroom_id=chatroom_id, agent_id=agent_id,
        ).on_conflict_do_nothing()
        await self._db.execute(stmt)

    async def remove(self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        await self._db.execute(
            t.chatroom_agents.delete().where(
                sa.and_(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                    t.chatroom_agents.c.agent_id == agent_id,
                )
            )
        )

    async def list(self, chatroom_id: uuid.UUID) -> Sequence[ChatroomAgent]:
        rows = (
            await self._db.execute(
                t.chatroom_agents.select().where(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                )
            )
        ).all()
        return [
            ChatroomAgent(chatroom_id=r.chatroom_id, agent_id=r.agent_id)
            for r in rows
        ]

    async def is_registered(
        self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID,
    ) -> bool:
        row = (
            await self._db.execute(
                sa.select(sa.literal(1)).select_from(t.chatroom_agents).where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.agent_id == agent_id,
                    )
                )
            )
        ).first()
        return row is not None


class ChatroomGuestRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        chatroom_id: uuid.UUID,
        user_id: uuid.UUID,
        joined_via_token: str,
    ) -> None:
        stmt = pg.insert(t.chatroom_guests).values(
            chatroom_id=chatroom_id,
            user_id=user_id,
            joined_via_token=joined_via_token,
        ).on_conflict_do_nothing()
        await self._db.execute(stmt)

    async def is_guest(
        self, *, chatroom_id: uuid.UUID, user_id: uuid.UUID,
    ) -> bool:
        row = (
            await self._db.execute(
                sa.select(sa.literal(1)).select_from(t.chatroom_guests).where(
                    sa.and_(
                        t.chatroom_guests.c.chatroom_id == chatroom_id,
                        t.chatroom_guests.c.user_id == user_id,
                    )
                )
            )
        ).first()
        return row is not None

    async def list(self, chatroom_id: uuid.UUID) -> Sequence[ChatroomGuest]:
        rows = (
            await self._db.execute(
                t.chatroom_guests.select().where(
                    t.chatroom_guests.c.chatroom_id == chatroom_id,
                )
            )
        ).all()
        return [
            ChatroomGuest(
                chatroom_id=r.chatroom_id,
                user_id=r.user_id,
                joined_via_token=r.joined_via_token,
                joined_at=r.joined_at,
            )
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# Message
# --------------------------------------------------------------------------- #


def _row_to_message(row: Any) -> Message:
    return Message(
        id=row.id,
        chatroom_id=row.chatroom_id,
        sender_type=SenderType(row.sender_type),
        sender_id=row.sender_id,
        content_md=row.content_md,
        metadata=dict(row.metadata or {}),
        version=row.version,
        created_at=row.created_at,
        edited_at=row.edited_at,
        deleted_at=row.deleted_at,
    )


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        chatroom_id: uuid.UUID,
        sender_type: SenderType,
        sender_id: uuid.UUID | None,
        content_md: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        row = (
            await self._db.execute(
                t.messages.insert()
                .values(
                    chatroom_id=chatroom_id,
                    sender_type=sender_type.value,
                    sender_id=sender_id,
                    content_md=content_md,
                    metadata=metadata or {},
                )
                .returning(t.messages)
            )
        ).one()
        return _row_to_message(row)

    async def get(self, message_id: uuid.UUID) -> Message | None:
        row = (
            await self._db.execute(
                t.messages.select().where(
                    sa.and_(
                        t.messages.c.id == message_id,
                        t.messages.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_message(row) if row else None

    async def list(
        self,
        *,
        chatroom_id: uuid.UUID,
        before: uuid.UUID | None = None,
        since: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[Message]:
        predicate: sa.ColumnElement[bool] = sa.and_(
            t.messages.c.chatroom_id == chatroom_id,
            t.messages.c.deleted_at.is_(None),
        )
        # Default: newest-first (before-cursor direction).
        order_cols: list[Any] = [
            t.messages.c.created_at.desc(), t.messages.c.id.desc()
        ]
        if before is not None:
            anchor = (
                await self._db.execute(
                    sa.select(t.messages.c.created_at, t.messages.c.id).where(
                        t.messages.c.id == before,
                    )
                )
            ).first()
            if anchor is None:
                raise ValueError(f"cursor 'before' message {before} not found")
            predicate = sa.and_(
                predicate,
                sa.or_(
                    t.messages.c.created_at < anchor.created_at,
                    sa.and_(
                        t.messages.c.created_at == anchor.created_at,
                        t.messages.c.id < anchor.id,
                    ),
                ),
            )
        if since is not None:
            anchor = (
                await self._db.execute(
                    sa.select(t.messages.c.created_at, t.messages.c.id).where(
                        t.messages.c.id == since,
                    )
                )
            ).first()
            if anchor is None:
                raise ValueError(f"cursor 'since' message {since} not found")
            predicate = sa.and_(
                predicate,
                sa.or_(
                    t.messages.c.created_at > anchor.created_at,
                    sa.and_(
                        t.messages.c.created_at == anchor.created_at,
                        t.messages.c.id > anchor.id,
                    ),
                ),
            )
            order_cols = [t.messages.c.created_at.asc(), t.messages.c.id.asc()]
        rows = (
            await self._db.execute(
                t.messages.select().where(predicate).order_by(*order_cols).limit(limit)
            )
        ).all()
        return [_row_to_message(r) for r in rows]

    async def update_content(
        self,
        *,
        message_id: uuid.UUID,
        expected_version: int,
        new_content_md: str,
    ) -> Message:
        stmt = (
            t.messages.update()
            .where(
                sa.and_(
                    t.messages.c.id == message_id,
                    t.messages.c.version == expected_version,
                    t.messages.c.deleted_at.is_(None),
                )
            )
            .values(
                content_md=new_content_md,
                edited_at=now(),
                version=t.messages.c.version + 1,
            )
            .returning(t.messages)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            raise VersionMismatch(
                f"message {message_id} version mismatch or missing",
            )
        return _row_to_message(row)

    async def hard_delete(self, message_id: uuid.UUID) -> int:
        """Hard-delete a message row. `message_edits` rows cascade via FK."""
        result = await self._db.execute(
            t.messages.delete().where(t.messages.c.id == message_id),
        )
        return result.rowcount or 0

    async def search(
        self,
        *,
        chatroom_id: uuid.UUID,
        query: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[tuple[Message, float, str]]:
        """Full-text search within a chatroom (F.10 / R13.18).

        Returns (message, rank, snippet) triples ordered by ts_rank_cd
        descending. `plainto_tsquery` is used so the caller's input is
        treated as literal text — there is no QUERY DSL injection surface.
        The `english` configuration matches the trigger in 0017; any
        localisation overrides land in that migration (F.10 follow-up).
        """
        tsq = sa.func.plainto_tsquery(sa.literal("english"), sa.literal(query))
        stmt = (
            sa.select(
                t.messages,
                sa.func.ts_rank_cd(t.messages.c.content_tsv, tsq).label("rank"),
                sa.func.ts_headline(
                    sa.literal("english"),
                    t.messages.c.content_md,
                    tsq,
                    sa.literal("MaxWords=35,MinWords=15,ShortWord=3"),
                ).label("snippet"),
            )
            .where(
                sa.and_(
                    t.messages.c.chatroom_id == chatroom_id,
                    t.messages.c.deleted_at.is_(None),
                    t.messages.c.content_tsv.op("@@")(tsq),
                )
            )
            .order_by(sa.desc("rank"))
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._db.execute(stmt)).all()
        out: list[tuple[Message, float, str]] = []
        for r in rows:
            msg = _row_to_message(r)
            out.append((msg, float(r.rank or 0.0), str(r.snippet or "")))
        return out

    async def all_for_chatroom(
        self, chatroom_id: uuid.UUID,
    ) -> Sequence[Message]:
        """Full dump of a room's live messages, ordered chronologically —
        used by the export worker. Always streams from the primary index so
        a 10k-message export stays sub-second."""
        rows = (
            await self._db.execute(
                t.messages.select()
                .where(
                    sa.and_(
                        t.messages.c.chatroom_id == chatroom_id,
                        t.messages.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.messages.c.created_at.asc())
            )
        ).all()
        return [_row_to_message(r) for r in rows]


class MessageEditRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record(
        self,
        *,
        message_id: uuid.UUID,
        old_content_md: str,
        edited_by_user_id: uuid.UUID,
    ) -> MessageEdit:
        row = (
            await self._db.execute(
                t.message_edits.insert()
                .values(
                    message_id=message_id,
                    old_content_md=old_content_md,
                    edited_by_user_id=edited_by_user_id,
                )
                .returning(t.message_edits)
            )
        ).one()
        return MessageEdit(
            id=row.id,
            message_id=row.message_id,
            old_content_md=row.old_content_md,
            edited_by_user_id=row.edited_by_user_id,
            edited_at=row.edited_at,
        )

    async def list_for_message(
        self, message_id: uuid.UUID,
    ) -> Sequence[MessageEdit]:
        rows = (
            await self._db.execute(
                t.message_edits.select()
                .where(t.message_edits.c.message_id == message_id)
                .order_by(t.message_edits.c.edited_at)
            )
        ).all()
        return [
            MessageEdit(
                id=r.id,
                message_id=r.message_id,
                old_content_md=r.old_content_md,
                edited_by_user_id=r.edited_by_user_id,
                edited_at=r.edited_at,
            )
            for r in rows
        ]


def _row_to_attachment(r: Any) -> MessageAttachment:
    return MessageAttachment(
        id=r.id,
        message_id=r.message_id,
        filename=r.filename,
        mime=r.mime,
        size_bytes=r.size_bytes,
        minio_path=r.minio_path,
        status=AttachmentStatus(r.status),
        scan_status=ScanStatus(r.scan_status),
        scan_at=r.scan_at,
        expires_at=r.expires_at,
        chatroom_id=getattr(r, "chatroom_id", None),
        uploaded_by_user_id=getattr(r, "uploaded_by_user_id", None),
    )


class MessageAttachmentRepository:
    """Populated by F.5 (single-shot + TUS completion). The `message_id`
    column is NULL between upload and the user's first send referencing the
    attachment, so every mutation carries `chatroom_id` + uploader so ACL
    checks can run against the attachment row directly."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        attachment_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploaded_by_user_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        minio_path: str,
        expires_at: datetime | None,
    ) -> MessageAttachment:
        row = (
            await self._db.execute(
                t.message_attachments.insert()
                .values(
                    id=attachment_id,
                    chatroom_id=chatroom_id,
                    uploaded_by_user_id=uploaded_by_user_id,
                    filename=filename,
                    mime=mime,
                    size_bytes=size_bytes,
                    minio_path=minio_path,
                    expires_at=expires_at,
                )
                .returning(t.message_attachments)
            )
        ).one()
        return _row_to_attachment(row)

    async def get(self, attachment_id: uuid.UUID) -> MessageAttachment | None:
        row = (
            await self._db.execute(
                t.message_attachments.select().where(
                    t.message_attachments.c.id == attachment_id,
                )
            )
        ).first()
        return _row_to_attachment(row) if row else None

    async def list_for_message(
        self, message_id: uuid.UUID,
    ) -> Sequence[MessageAttachment]:
        rows = (
            await self._db.execute(
                t.message_attachments.select().where(
                    t.message_attachments.c.message_id == message_id,
                )
            )
        ).all()
        return [_row_to_attachment(r) for r in rows]

    async def bind_to_message(
        self,
        *,
        attachment_ids: "Sequence[uuid.UUID] | list[uuid.UUID]",
        message_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        uploaded_by_user_id: uuid.UUID,
    ) -> int:
        """Bind unbound attachments to a message.

        The WHERE clause intentionally re-asserts `chatroom_id` and the
        uploader — binding SOMEONE ELSE's upload, or an upload from a
        different room, must be impossible even if the caller supplies a
        valid UUID. Returns the number of rows actually updated so the
        caller can detect partial mismatches.
        """
        if not attachment_ids:
            return 0
        result = await self._db.execute(
            t.message_attachments.update()
            .where(
                sa.and_(
                    t.message_attachments.c.id.in_(list(attachment_ids)),
                    t.message_attachments.c.message_id.is_(None),
                    t.message_attachments.c.chatroom_id == chatroom_id,
                    t.message_attachments.c.uploaded_by_user_id == uploaded_by_user_id,
                    t.message_attachments.c.status == AttachmentStatus.ACTIVE.value,
                )
            )
            .values(message_id=message_id),
        )
        return result.rowcount or 0

    async def mark_scan(
        self,
        *,
        attachment_id: uuid.UUID,
        scan_status: ScanStatus,
        scan_at: datetime,
    ) -> None:
        values: dict[str, Any] = {
            "scan_status": scan_status.value,
            "scan_at": scan_at,
        }
        if scan_status is ScanStatus.QUARANTINED:
            values["status"] = AttachmentStatus.QUARANTINED.value
        await self._db.execute(
            t.message_attachments.update()
            .where(t.message_attachments.c.id == attachment_id)
            .values(**values),
        )

    async def mark_expired(self, attachment_id: uuid.UUID) -> None:
        await self._db.execute(
            t.message_attachments.update()
            .where(t.message_attachments.c.id == attachment_id)
            .values(status=AttachmentStatus.EXPIRED.value),
        )

    async def list_expired(
        self, *, horizon: datetime, limit: int = 500,
    ) -> Sequence[MessageAttachment]:
        rows = (
            await self._db.execute(
                t.message_attachments.select()
                .where(
                    sa.and_(
                        t.message_attachments.c.expires_at.is_not(None),
                        t.message_attachments.c.expires_at < horizon,
                        t.message_attachments.c.status
                            != AttachmentStatus.EXPIRED.value,
                    )
                )
                .limit(limit)
            )
        ).all()
        return [_row_to_attachment(r) for r in rows]

    async def list_for_chatroom_older_than(
        self, *, chatroom_id: uuid.UUID, horizon: datetime,
    ) -> Sequence[MessageAttachment]:
        """Used by the retention sweep: find attachments belonging to messages
        older than the retention horizon. Joins via message_id so orphan rows
        (message_id NULL) are left alone — those are handled by `list_expired`
        driven by the bucket's 3-day lifecycle."""
        rows = (
            await self._db.execute(
                t.message_attachments.select()
                .select_from(
                    t.message_attachments.join(
                        t.messages,
                        t.message_attachments.c.message_id == t.messages.c.id,
                    )
                )
                .where(
                    sa.and_(
                        t.messages.c.chatroom_id == chatroom_id,
                        t.messages.c.created_at < horizon,
                    )
                )
            )
        ).all()
        return [_row_to_attachment(r) for r in rows]


__all__ = [
    "ChatroomAgentRepository",
    "ChatroomGuestRepository",
    "ChatroomRepository",
    "MessageAttachmentRepository",
    "MessageEditRepository",
    "MessageRepository",
    "WorkspaceRepository",
    "_new_guest_token",
]
