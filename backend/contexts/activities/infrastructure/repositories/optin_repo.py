"""Async repository for ``project_activity_type_optins`` ([R30.33]).

The opt-in row is an authorization record, not a preference: it is what makes a
platform-scoped type reachable from one project, and it is read on every
room-level path rather than only when rendering a picker. Everything here is
therefore deliberately narrow — exists, list, add, remove — so there is no query
shape that could accidentally answer "reachable" more permissively than
``exists`` does.

``remove_all_for_type`` is the one method not bounded by a project, and it does
not breach that rule: the narrowness protects the *reachability answer*, and a
delete only ever revokes access. It cannot widen what ``exists`` returns for any
project, which is the property the rule exists to hold.

Caller owns commit, as everywhere else in this context.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import ProjectActivityTypeOptIn
from contexts.activities.infrastructure import tables as t
from shared_kernel.db.rowcount import rowcount

_OPTIN_COLS = (
    t.project_activity_type_optins.c.project_id,
    t.project_activity_type_optins.c.activity_type_id,
    t.project_activity_type_optins.c.enabled_by_user_id,
    t.project_activity_type_optins.c.created_at,
)


def _row_to_optin(row: object) -> ProjectActivityTypeOptIn:
    return ProjectActivityTypeOptIn(
        project_id=row.project_id,  # type: ignore[attr-defined]
        activity_type_id=row.activity_type_id,  # type: ignore[attr-defined]
        enabled_by_user_id=row.enabled_by_user_id,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
    )


class ProjectActivityTypeOptInRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def exists(self, *, project_id: uuid.UUID, activity_type_id: uuid.UUID) -> bool:
        """Whether this project has opted into this type — the authorization read.

        A bare ``SELECT 1`` against the composite primary key: this runs on every
        activation, session open, and submission of a platform type, so it must
        not grow into a row fetch.
        """
        found = (
            await self._db.execute(
                sa.select(sa.literal(1)).where(
                    sa.and_(
                        t.project_activity_type_optins.c.project_id == project_id,
                        t.project_activity_type_optins.c.activity_type_id == activity_type_id,
                    )
                )
            )
        ).first()
        return found is not None

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[ProjectActivityTypeOptIn]:
        """Every opt-in this project holds, for the import dialog's enabled state."""
        rows = (
            await self._db.execute(
                sa.select(*_OPTIN_COLS).where(t.project_activity_type_optins.c.project_id == project_id)
            )
        ).all()
        return [_row_to_optin(r) for r in rows]

    async def add(
        self, *, project_id: uuid.UUID, activity_type_id: uuid.UUID, enabled_by_user_id: uuid.UUID
    ) -> bool:
        """Record an opt-in; returns False when one already existed.

        ``ON CONFLICT DO NOTHING`` rather than a read-then-write: a double click
        must be a no-op, not a 500, and the caller uses the return value to decide
        whether to emit an audit event for something that actually changed.
        """
        result = await self._db.execute(
            pg_insert(t.project_activity_type_optins)
            .values(
                project_id=project_id,
                activity_type_id=activity_type_id,
                enabled_by_user_id=enabled_by_user_id,
            )
            .on_conflict_do_nothing()
        )
        return bool(rowcount(result))

    async def remove(self, *, project_id: uuid.UUID, activity_type_id: uuid.UUID) -> bool:
        """Revoke an opt-in; returns False when there was none."""
        result = await self._db.execute(
            t.project_activity_type_optins.delete().where(
                sa.and_(
                    t.project_activity_type_optins.c.project_id == project_id,
                    t.project_activity_type_optins.c.activity_type_id == activity_type_id,
                )
            )
        )
        return bool(rowcount(result))

    async def remove_all_for_type(self, activity_type_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """Revoke every project's opt-in for one type; returns the projects revoked.

        Unbounded by project on purpose, and only correct for an admin delete —
        the type is going away for everyone. One project revoking its own access
        is :meth:`remove`, and the two must never be confused: this one would end
        every other tenant's access.

        The type delete is soft, so the FK's ``ON DELETE CASCADE`` never fires and
        this is what actually removes the rows. ``RETURNING project_id`` costs
        nothing over a bare DELETE and is what lets the caller record the blast
        radius on the delete's audit event.
        """
        result = await self._db.execute(
            t.project_activity_type_optins.delete()
            .where(t.project_activity_type_optins.c.activity_type_id == activity_type_id)
            .returning(t.project_activity_type_optins.c.project_id)
        )
        return list(result.scalars().all())


__all__ = ["ProjectActivityTypeOptInRepository"]
