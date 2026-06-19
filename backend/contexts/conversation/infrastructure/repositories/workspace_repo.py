"""Workspace repository -- data access for workspace entities."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.errors import NameTaken
from contexts.conversation.domain.models import Workspace
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now


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
        from sqlalchemy.exc import IntegrityError

        try:
            row = (
                await self._db.execute(
                    t.workspaces.insert().values(project_id=project_id, name=name).returning(t.workspaces)
                )
            ).one()
        except IntegrityError as exc:  # pragma: no cover -- no unique index today
            raise NameTaken(name) from exc
        return _row_to_workspace(row)

    async def get(
        self,
        workspace_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Workspace | None:
        predicate = t.workspaces.c.id == workspace_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.workspaces.c.deleted_at.is_(None))
        row = (await self._db.execute(t.workspaces.select().where(predicate))).first()
        return _row_to_workspace(row) if row else None

    async def list_for_project(
        self,
        project_id: uuid.UUID,
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
            t.workspaces.update().where(t.workspaces.c.id == workspace_id).values(deleted_at=now())
        )


__all__ = ["WorkspaceRepository"]
