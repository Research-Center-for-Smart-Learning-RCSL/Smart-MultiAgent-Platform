"""Async repository for ``activity_types`` (Chapter §30, R30.02).

All writes keep the caller's :class:`AsyncSession`; the service/route owns commit
so audit rows and the type row stay atomic. Reads filter ``deleted_at IS NULL``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.errors import ActivityTypeKeyConflict
from contexts.activities.domain.models import ActivityType, ValidatorKind
from contexts.activities.infrastructure import tables as t
from shared_kernel.auth.clients import now
from shared_kernel.db.rowcount import rowcount

_TYPE_COLS = (
    t.activity_types.c.id,
    t.activity_types.c.project_id,
    t.activity_types.c.key,
    t.activity_types.c.name,
    t.activity_types.c.payload_schema,
    t.activity_types.c.validator_kind,
    t.activity_types.c.validator_config,
    t.activity_types.c.retention_days,
    t.activity_types.c.version,
    t.activity_types.c.created_at,
    t.activity_types.c.deleted_at,
)


def _row_to_type(row: object) -> ActivityType:
    return ActivityType(
        id=row.id,  # type: ignore[attr-defined]
        project_id=row.project_id,  # type: ignore[attr-defined]
        key=row.key,  # type: ignore[attr-defined]
        name=row.name,  # type: ignore[attr-defined]
        payload_schema=dict(row.payload_schema or {}),  # type: ignore[attr-defined]
        validator_kind=ValidatorKind(row.validator_kind),  # type: ignore[attr-defined]
        validator_config=dict(row.validator_config or {}),  # type: ignore[attr-defined]
        retention_days=row.retention_days,  # type: ignore[attr-defined]
        version=row.version,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        deleted_at=row.deleted_at,  # type: ignore[attr-defined]
    )


class ActivityTypeRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        key: str,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
    ) -> uuid.UUID:
        """Insert a type and return its id (caller owns commit).

        The ``uq_activity_types_project_key_active`` partial-unique maps a
        duplicate live key in the project to a domain 409; any *other*
        IntegrityError (e.g. a stale ``project_id`` FK) is re-raised as its true
        cause rather than mismapped to a key conflict.
        """
        try:
            row = await self._db.execute(
                t.activity_types.insert()
                .values(
                    project_id=project_id,
                    key=key,
                    name=name,
                    payload_schema=payload_schema,
                    validator_kind=validator_kind.value,
                    validator_config=validator_config,
                    retention_days=retention_days,
                )
                .returning(t.activity_types.c.id)
            )
        except IntegrityError as exc:
            msg = str(exc.orig or exc).lower()
            if "uq_activity_types_project_key_active" in msg:
                raise ActivityTypeKeyConflict(
                    f"activity type key {key!r} already exists in project {project_id}"
                ) from exc
            raise
        return uuid.UUID(str(row.scalar_one()))

    async def get(self, type_id: uuid.UUID) -> ActivityType | None:
        """A single live type, or ``None`` if missing/soft-deleted."""
        row = (
            await self._db.execute(
                sa.select(*_TYPE_COLS).where(
                    sa.and_(
                        t.activity_types.c.id == type_id,
                        t.activity_types.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_type(row) if row is not None else None

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        """Live types in a project, newest first (id tiebreak for stable paging)."""
        rows = (
            await self._db.execute(
                sa.select(*_TYPE_COLS)
                .where(
                    sa.and_(
                        t.activity_types.c.project_id == project_id,
                        t.activity_types.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.activity_types.c.created_at.desc(), t.activity_types.c.id.desc())
            )
        ).all()
        return [_row_to_type(r) for r in rows]

    async def update(
        self,
        type_id: uuid.UUID,
        *,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
        bump_version: bool,
    ) -> bool:
        """Replace a live type's editable fields (``key`` excluded); the
        ``deleted_at IS NULL`` guard makes an update of a tombstoned type a no-op
        (0 rows). ``version`` increments only when ``bump_version`` is set — the
        service passes it when a behavioral field actually changed (R30.23)."""
        values: dict[str, Any] = {
            "name": name,
            "payload_schema": payload_schema,
            "validator_kind": validator_kind.value,
            "validator_config": validator_config,
            "retention_days": retention_days,
        }
        if bump_version:
            values["version"] = t.activity_types.c.version + 1
        result = await self._db.execute(
            t.activity_types.update()
            .where(
                sa.and_(
                    t.activity_types.c.id == type_id,
                    t.activity_types.c.deleted_at.is_(None),
                )
            )
            .values(**values)
        )
        return bool(rowcount(result))

    async def soft_delete(self, type_id: uuid.UUID) -> bool:
        """Tombstone a type; the ``deleted_at IS NULL`` guard makes a
        double-delete a no-op (0 rows)."""
        result = await self._db.execute(
            t.activity_types.update()
            .where(
                sa.and_(
                    t.activity_types.c.id == type_id,
                    t.activity_types.c.deleted_at.is_(None),
                )
            )
            .values(deleted_at=now())
        )
        return bool(rowcount(result))


__all__ = ["ActivityTypeRepository"]
