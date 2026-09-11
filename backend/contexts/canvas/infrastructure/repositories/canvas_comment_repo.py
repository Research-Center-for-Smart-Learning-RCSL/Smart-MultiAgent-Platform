"""Canvas comment repository -- data access for canvas object comments."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.models import CanvasComment
from contexts.canvas.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_comment(row: Any) -> CanvasComment:
    return CanvasComment(
        id=row.id,
        canvas_id=row.canvas_id,
        object_id=row.object_id,
        content=row.content,
        created_by_user_id=row.created_by_user_id,
        created_by_guest_id=row.created_by_guest_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class CanvasCommentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, comment_id: uuid.UUID, *, canvas_id: uuid.UUID) -> CanvasComment | None:
        row = (
            await self._db.execute(
                t.canvas_object_comments.select().where(
                    sa.and_(
                        t.canvas_object_comments.c.id == comment_id,
                        t.canvas_object_comments.c.canvas_id == canvas_id,
                        t.canvas_object_comments.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_comment(row) if row else None

    async def list_comments(
        self,
        object_id: uuid.UUID,
        *,
        canvas_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[CanvasComment]:
        rows = (
            await self._db.execute(
                t.canvas_object_comments.select()
                .where(
                    sa.and_(
                        t.canvas_object_comments.c.object_id == object_id,
                        t.canvas_object_comments.c.canvas_id == canvas_id,
                        t.canvas_object_comments.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.canvas_object_comments.c.created_at)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_comment(r) for r in rows]

    async def create_comment(self, *, values: dict[str, Any]) -> CanvasComment:
        row = (
            await self._db.execute(
                t.canvas_object_comments.insert().values(**values).returning(t.canvas_object_comments)
            )
        ).one()
        return _row_to_comment(row)

    async def update_comment(
        self,
        comment_id: uuid.UUID,
        *,
        canvas_id: uuid.UUID,
        content: str,
    ) -> CanvasComment | None:
        row = (
            await self._db.execute(
                t.canvas_object_comments.update()
                .where(
                    sa.and_(
                        t.canvas_object_comments.c.id == comment_id,
                        t.canvas_object_comments.c.canvas_id == canvas_id,
                        t.canvas_object_comments.c.deleted_at.is_(None),
                    )
                )
                .values(content=content, updated_at=now())
                .returning(t.canvas_object_comments)
            )
        ).first()
        return _row_to_comment(row) if row else None

    async def delete_comment(self, comment_id: uuid.UUID, *, canvas_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            t.canvas_object_comments.update()
            .where(
                sa.and_(
                    t.canvas_object_comments.c.id == comment_id,
                    t.canvas_object_comments.c.canvas_id == canvas_id,
                    t.canvas_object_comments.c.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now())
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def count_comments_by_object(
        self, object_ids: Sequence[uuid.UUID], *, canvas_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        if not object_ids:
            return {}
        rows = (
            await self._db.execute(
                sa.select(
                    t.canvas_object_comments.c.object_id,
                    sa.func.count().label("cnt"),
                )
                .where(
                    sa.and_(
                        t.canvas_object_comments.c.canvas_id == canvas_id,
                        t.canvas_object_comments.c.object_id.in_(object_ids),
                        t.canvas_object_comments.c.deleted_at.is_(None),
                    )
                )
                .group_by(t.canvas_object_comments.c.object_id)
            )
        ).all()
        return {row.object_id: row.cnt for row in rows}


__all__ = ["CanvasCommentRepository"]
