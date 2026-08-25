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
from contexts.activities.domain.models import ActivityType, ActivityTypeScope, ValidatorKind
from contexts.activities.infrastructure import tables as t
from shared_kernel.auth.clients import now
from shared_kernel.db.rowcount import rowcount

_TYPE_COLS = (
    t.activity_types.c.id,
    t.activity_types.c.project_id,
    t.activity_types.c.scope,
    t.activity_types.c.key,
    t.activity_types.c.name,
    t.activity_types.c.payload_schema,
    t.activity_types.c.validator_kind,
    t.activity_types.c.validator_config,
    t.activity_types.c.retention_days,
    t.activity_types.c.version,
    t.activity_types.c.created_at,
    t.activity_types.c.expose_payload_to_agent,
    t.activity_types.c.echo_includes_content,
    t.activity_types.c.deleted_at,
    t.activity_types.c.group_config,
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
        expose_payload_to_agent=row.expose_payload_to_agent,  # type: ignore[attr-defined]
        echo_includes_content=row.echo_includes_content,  # type: ignore[attr-defined]
        deleted_at=row.deleted_at,  # type: ignore[attr-defined]
        scope=ActivityTypeScope(row.scope),  # type: ignore[attr-defined]
        # Not coerced to `{}` like the other JSONB columns: NULL is meaningful
        # here (individual-only), and an empty object is not the same claim.
        group_config=(
            dict(row.group_config)  # type: ignore[attr-defined]
            if row.group_config is not None  # type: ignore[attr-defined]
            else None
        ),
    )


class ActivityTypeRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        project_id: uuid.UUID | None,
        key: str,
        name: str,
        payload_schema: dict[str, Any],
        validator_kind: ValidatorKind,
        validator_config: dict[str, Any],
        retention_days: int | None,
        expose_payload_to_agent: bool,
        echo_includes_content: bool,
        scope: ActivityTypeScope = ActivityTypeScope.PROJECT,
        group_config: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Insert a type and return its id (caller owns commit).

        Two partial-uniques guard the key, one per scope, and each maps to the
        same domain 409: ``uq_activity_types_project_key_active`` for a duplicate
        live key within a project, ``uq_activity_types_platform_key_active`` for a
        duplicate live platform key. Both names are matched because a NULL
        ``project_id`` makes the first index stop constraining platform rows
        (PostgreSQL treats every NULL as distinct), so the second is the only
        thing standing between a double install and two types sharing a key.

        Any *other* IntegrityError (e.g. a stale ``project_id`` FK, or the
        scope/project CHECK) is re-raised as its true cause rather than mismapped
        to a key conflict.
        """
        try:
            row = await self._db.execute(
                t.activity_types.insert()
                .values(
                    project_id=project_id,
                    scope=scope.value,
                    key=key,
                    name=name,
                    payload_schema=payload_schema,
                    validator_kind=validator_kind.value,
                    validator_config=validator_config,
                    retention_days=retention_days,
                    expose_payload_to_agent=expose_payload_to_agent,
                    echo_includes_content=echo_includes_content,
                    group_config=group_config,
                )
                .returning(t.activity_types.c.id)
            )
        except IntegrityError as exc:
            msg = str(exc.orig or exc).lower()
            if "uq_activity_types_project_key_active" in msg:
                raise ActivityTypeKeyConflict(
                    f"activity type key {key!r} already exists in project {project_id}"
                ) from exc
            if "uq_activity_types_platform_key_active" in msg:
                raise ActivityTypeKeyConflict(f"platform activity type key {key!r} already exists") from exc
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

    async def get_many(self, type_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ActivityType]:
        """Batch-resolve live types by id, keyed by id (N+1-free name lookups)."""
        ids = list(type_ids)
        if not ids:
            return {}
        rows = (
            await self._db.execute(
                sa.select(*_TYPE_COLS).where(
                    sa.and_(
                        t.activity_types.c.id.in_(ids),
                        t.activity_types.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        types = [_row_to_type(r) for r in rows]
        return {at.id: at for at in types}

    async def list_all(self, *, cursor: uuid.UUID | None = None, limit: int = 50) -> Sequence[ActivityType]:
        """Live types across every project, newest first, keyset-paginated ([R30.31]).

        Unscoped by design and reachable only from the admin surface. Keyset rather
        than offset because this table grows with the whole platform, and a deep
        offset scan is what keyset pagination exists to avoid. The cursor is the
        last row's id; ``created_at`` alone is not unique, so a correlated subquery
        supplies the anchor and ``id`` breaks the tie.

        The comparison is decomposed into OR/AND rather than written as a row-value
        tuple: that is the shape already proven in ``admin_projects.list_projects``,
        and a tuple comparison would be a construct the unit tier cannot verify
        (it compiles with ``literal_binds``, so a parameter-type error surfaces only
        against a real database).
        """
        stmt = sa.select(*_TYPE_COLS).where(t.activity_types.c.deleted_at.is_(None))
        if cursor is not None:
            anchor = (
                sa.select(t.activity_types.c.created_at)
                .where(t.activity_types.c.id == cursor)
                .scalar_subquery()
            )
            stmt = stmt.where(
                sa.or_(
                    t.activity_types.c.created_at < anchor,
                    sa.and_(
                        t.activity_types.c.created_at == anchor,
                        t.activity_types.c.id < cursor,
                    ),
                )
            )
        stmt = stmt.order_by(t.activity_types.c.created_at.desc(), t.activity_types.c.id.desc()).limit(limit)
        rows = (await self._db.execute(stmt)).all()
        return [_row_to_type(r) for r in rows]

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        """Live types usable by a project, newest first (id tiebreak for stable paging).

        Two populations in one query ([R30.33]): the project's own types, and the
        platform types this project has opted into. The opt-in subquery rather
        than an OUTER JOIN keeps the row shape identical to the project-only case,
        so no de-duplication is needed and ``_row_to_type`` is unchanged.

        This is a *presentation* filter. It is not the tenancy gate — the room
        paths cannot rely on a listing, since they take an ``activity_type_id``
        straight from the client body. See ``application/reachability.py``.
        """
        opted_in = sa.select(t.project_activity_type_optins.c.activity_type_id).where(
            t.project_activity_type_optins.c.project_id == project_id
        )
        rows = (
            await self._db.execute(
                sa.select(*_TYPE_COLS)
                .where(
                    sa.and_(
                        t.activity_types.c.deleted_at.is_(None),
                        sa.or_(
                            t.activity_types.c.project_id == project_id,
                            t.activity_types.c.id.in_(opted_in),
                        ),
                    )
                )
                .order_by(t.activity_types.c.created_at.desc(), t.activity_types.c.id.desc())
            )
        ).all()
        return [_row_to_type(r) for r in rows]

    async def list_owned_by_project(self, project_id: uuid.UUID) -> Sequence[ActivityType]:
        """Live types this project **owns**, newest first.

        Not the usable set — that is :meth:`list_for_project`, which also admits
        the platform types this project opted into. The distinction matters to any
        caller asking "does this project already have its own type under this key":
        a platform row is read-only to a Project Owner, so it is not a substitute
        for one they own ([R30.02], [R30.33]).
        """
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

    async def list_platform(self) -> Sequence[ActivityType]:
        """Every live platform-scoped type, newest first ([R30.32]).

        Unbounded on purpose: platform types are installed one course at a time by
        an admin, so the population is bounded by deliberate acts rather than by
        tenant traffic. The admin listing's keyset pagination exists because
        ``list_all`` grows with the whole platform; this does not.
        """
        rows = (
            await self._db.execute(
                sa.select(*_TYPE_COLS)
                .where(
                    sa.and_(
                        t.activity_types.c.project_id.is_(None),
                        t.activity_types.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.activity_types.c.created_at.desc(), t.activity_types.c.id.desc())
            )
        ).all()
        return [_row_to_type(r) for r in rows]

    async def list_platform_by_keys(self, keys: Sequence[str]) -> Sequence[ActivityType]:
        """Live platform types matching any of ``keys`` — the install idempotency
        read, so a re-install reports already-present instead of conflicting."""
        wanted = list(keys)
        if not wanted:
            return []
        rows = (
            await self._db.execute(
                sa.select(*_TYPE_COLS).where(
                    sa.and_(
                        t.activity_types.c.project_id.is_(None),
                        t.activity_types.c.key.in_(wanted),
                        t.activity_types.c.deleted_at.is_(None),
                    )
                )
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
        expose_payload_to_agent: bool,
        echo_includes_content: bool,
        bump_version: bool,
        group_config: dict[str, Any] | None = None,
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
            "expose_payload_to_agent": expose_payload_to_agent,
            "echo_includes_content": echo_includes_content,
            "group_config": group_config,
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
