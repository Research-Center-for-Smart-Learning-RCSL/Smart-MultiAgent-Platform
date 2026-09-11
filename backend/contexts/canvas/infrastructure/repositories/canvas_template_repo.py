"""Canvas template repository -- data access for canvas templates."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.domain.models import CanvasTemplate, CanvasTemplateScope
from contexts.canvas.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_template(row: Any) -> CanvasTemplate:
    return CanvasTemplate(
        id=row.id,
        scope=CanvasTemplateScope(row.scope),
        project_id=row.project_id,
        name=row.name,
        description=row.description,
        template_data=row.template_data,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


class CanvasTemplateRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_templates(
        self,
        *,
        scope: CanvasTemplateScope | None = None,
        project_id: uuid.UUID | None = None,
    ) -> Sequence[CanvasTemplate]:
        conditions: list[sa.ColumnElement[bool]] = [t.canvas_templates.c.deleted_at.is_(None)]
        if scope is not None:
            conditions.append(t.canvas_templates.c.scope == scope.value)
        if project_id is not None:
            conditions.append(
                sa.or_(
                    t.canvas_templates.c.scope == CanvasTemplateScope.PLATFORM.value,
                    t.canvas_templates.c.project_id == project_id,
                )
            )
        elif scope is None:
            conditions.append(t.canvas_templates.c.scope == CanvasTemplateScope.PLATFORM.value)

        rows = (
            await self._db.execute(
                t.canvas_templates.select()
                .where(sa.and_(*conditions))
                .order_by(t.canvas_templates.c.scope, t.canvas_templates.c.name)
            )
        ).all()
        return [_row_to_template(r) for r in rows]

    async def get(self, template_id: uuid.UUID) -> CanvasTemplate | None:
        row = (
            await self._db.execute(
                t.canvas_templates.select().where(
                    sa.and_(
                        t.canvas_templates.c.id == template_id,
                        t.canvas_templates.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_template(row) if row else None

    async def create(self, *, values: dict[str, Any]) -> CanvasTemplate:
        row = (
            await self._db.execute(t.canvas_templates.insert().values(**values).returning(t.canvas_templates))
        ).one()
        return _row_to_template(row)

    async def soft_delete(self, template_id: uuid.UUID) -> bool:
        result = await self._db.execute(
            t.canvas_templates.update()
            .where(
                sa.and_(
                    t.canvas_templates.c.id == template_id,
                    t.canvas_templates.c.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now())
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]


__all__ = ["CanvasTemplateRepository"]
