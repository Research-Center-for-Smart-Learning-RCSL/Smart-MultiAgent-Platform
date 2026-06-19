"""Message repositories -- data access for message and message-edit entities."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import VersionMismatch
from contexts.conversation.domain.models import (
    Message,
    MessageEdit,
    SenderType,
)
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now


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
        order_cols: list[Any] = [t.messages.c.created_at.desc(), t.messages.c.id.desc()]
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
            await self._db.execute(t.messages.select().where(predicate).order_by(*order_cols).limit(limit))
        ).all()
        return [_row_to_message(r) for r in rows]

    async def update_content(
        self,
        *,
        message_id: uuid.UUID,
        expected_version: int,
        new_content_md: str,
        max_age: timedelta | None = None,
    ) -> Message:
        conditions = [
            t.messages.c.id == message_id,
            t.messages.c.version == expected_version,
            t.messages.c.deleted_at.is_(None),
        ]
        if max_age is not None:
            # Atomic guard against the self-edit-window race: re-check the
            # window using DB clock inside the same UPDATE so a transaction
            # delayed past the deadline cannot succeed.
            conditions.append(
                t.messages.c.created_at >= sa.func.now() - max_age,
            )
        stmt = (
            t.messages.update()
            .where(sa.and_(*conditions))
            .values(
                # version bumped by the smap_bump_version trigger.
                content_md=new_content_md,
                edited_at=now(),
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
        treated as literal text -- there is no QUERY DSL injection surface.
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
        self,
        chatroom_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> Sequence[Message]:
        """Full dump of a room's live messages, ordered chronologically --
        used by the export worker. Always streams from the primary index so
        a 10k-message export stays sub-second.

        *limit* caps the result set to prevent unbounded memory usage in
        large rooms.
        """
        q = (
            t.messages.select()
            .where(
                sa.and_(
                    t.messages.c.chatroom_id == chatroom_id,
                    t.messages.c.deleted_at.is_(None),
                )
            )
            .order_by(t.messages.c.created_at.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        rows = (await self._db.execute(q)).all()
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
        self,
        message_id: uuid.UUID,
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


__all__ = ["MessageEditRepository", "MessageRepository"]
