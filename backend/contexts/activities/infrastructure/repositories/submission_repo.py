"""Async repository for ``activity_submissions`` (Chapter §30, R30.01/R30.10).

Owns attempt-count reads, the authoritative insert, the idempotent validation
write-backs (transition only from ``pending``), the observer read join, and the
single-query aggregate. All writes keep the caller's :class:`AsyncSession`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import (
    ActivityAggregate,
    ActivitySubmission,
    RecentActivityRow,
    ValidationStatus,
)
from contexts.activities.infrastructure import tables as t

_SUB = t.activity_submissions
_SESS = t.activity_sessions
_TYPE = t.activity_types

_SUB_COLS = (
    _SUB.c.id,
    _SUB.c.session_id,
    _SUB.c.activity_type_id,
    _SUB.c.chatroom_id,
    _SUB.c.producer_user_id,
    _SUB.c.payload,
    _SUB.c.attempt_no,
    _SUB.c.validation_status,
    _SUB.c.is_valid,
    _SUB.c.error_class,
    _SUB.c.sub_scores,
    _SUB.c.latency_ms,
    _SUB.c.retain_until,
    _SUB.c.created_at,
    _SUB.c.validated_at,
    _SUB.c.deleted_at,
)


def _row_to_submission(row: object) -> ActivitySubmission:
    return ActivitySubmission(
        id=row.id,  # type: ignore[attr-defined]
        session_id=row.session_id,  # type: ignore[attr-defined]
        activity_type_id=row.activity_type_id,  # type: ignore[attr-defined]
        chatroom_id=row.chatroom_id,  # type: ignore[attr-defined]
        producer_user_id=row.producer_user_id,  # type: ignore[attr-defined]
        payload=dict(row.payload or {}),  # type: ignore[attr-defined]
        attempt_no=row.attempt_no,  # type: ignore[attr-defined]
        validation_status=ValidationStatus(row.validation_status),  # type: ignore[attr-defined]
        is_valid=row.is_valid,  # type: ignore[attr-defined]
        error_class=row.error_class,  # type: ignore[attr-defined]
        sub_scores=dict(row.sub_scores or {}),  # type: ignore[attr-defined]
        latency_ms=row.latency_ms,  # type: ignore[attr-defined]
        retain_until=row.retain_until,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        validated_at=row.validated_at,  # type: ignore[attr-defined]
        deleted_at=row.deleted_at,  # type: ignore[attr-defined]
    )


class ActivitySubmissionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def count_in_session(self, session_id: uuid.UUID) -> int:
        """Number of live submissions in a session — the basis for the next
        ``attempt_no`` (called under the session ``FOR UPDATE`` lock)."""
        count = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(_SUB)
                .where(
                    sa.and_(
                        _SUB.c.session_id == session_id,
                        _SUB.c.deleted_at.is_(None),
                    )
                )
            )
        ).scalar_one()
        return int(count)

    async def insert(
        self,
        *,
        session_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        producer_user_id: uuid.UUID,
        payload: dict[str, Any],
        attempt_no: int,
        validation_status: ValidationStatus,
        is_valid: bool | None,
        error_class: str | None,
        sub_scores: dict[str, Any],
        latency_ms: int | None,
        retain_until: dt.datetime | None,
        validated_at: dt.datetime | None,
    ) -> uuid.UUID:
        row = await self._db.execute(
            _SUB.insert()
            .values(
                session_id=session_id,
                activity_type_id=activity_type_id,
                chatroom_id=chatroom_id,
                producer_user_id=producer_user_id,
                payload=payload,
                attempt_no=attempt_no,
                validation_status=validation_status.value,
                is_valid=is_valid,
                error_class=error_class,
                sub_scores=sub_scores,
                latency_ms=latency_ms,
                retain_until=retain_until,
                validated_at=validated_at,
            )
            .returning(_SUB.c.id)
        )
        return uuid.UUID(str(row.scalar_one()))

    async def get(self, submission_id: uuid.UUID) -> ActivitySubmission | None:
        row = (
            await self._db.execute(
                sa.select(*_SUB_COLS).where(sa.and_(_SUB.c.id == submission_id, _SUB.c.deleted_at.is_(None)))
            )
        ).first()
        return _row_to_submission(row) if row is not None else None

    async def record_validation(
        self,
        *,
        submission_id: uuid.UUID,
        is_valid: bool,
        error_class: str | None,
        sub_scores: dict[str, Any],
        latency_ms: int | None,
        validated_at: dt.datetime,
    ) -> bool:
        """Write a completed verdict — idempotent: transitions **only** from
        ``pending``, so a redelivered/retried job or a race with the watchdog is
        a no-op (0 rows)."""
        result = await self._db.execute(
            _SUB.update()
            .where(
                sa.and_(
                    _SUB.c.id == submission_id,
                    _SUB.c.validation_status == ValidationStatus.PENDING.value,
                )
            )
            .values(
                validation_status=ValidationStatus.VALIDATED.value,
                is_valid=is_valid,
                error_class=error_class,
                sub_scores=sub_scores,
                latency_ms=latency_ms,
                validated_at=validated_at,
            )
        )
        return bool(result.rowcount)

    async def record_error(
        self, *, submission_id: uuid.UUID, error_class: str, validated_at: dt.datetime
    ) -> bool:
        """Mark a submission ``error`` (validator could not produce a verdict) —
        pending-only, so it never clobbers a completed ``validated`` row."""
        result = await self._db.execute(
            _SUB.update()
            .where(
                sa.and_(
                    _SUB.c.id == submission_id,
                    _SUB.c.validation_status == ValidationStatus.PENDING.value,
                )
            )
            .values(
                validation_status=ValidationStatus.ERROR.value,
                error_class=error_class,
                validated_at=validated_at,
            )
        )
        return bool(result.rowcount)

    async def sweep_stalled(self, *, cutoff: dt.datetime, error_class: str, limit: int = 500) -> int:
        """Watchdog: move ``pending`` rows older than ``cutoff`` to ``error``.

        Bounded per call; the ``pending``-only predicate leaves ``validated`` and
        already-``error`` rows untouched (R30.06)."""
        batch = (
            sa.select(_SUB.c.id)
            .where(
                sa.and_(
                    _SUB.c.validation_status == ValidationStatus.PENDING.value,
                    _SUB.c.created_at < cutoff,
                )
            )
            .limit(limit)
        )
        result = await self._db.execute(
            _SUB.update()
            .where(
                sa.and_(
                    _SUB.c.validation_status == ValidationStatus.PENDING.value,
                    _SUB.c.id.in_(batch),
                )
            )
            .values(
                validation_status=ValidationStatus.ERROR.value,
                error_class=error_class,
                validated_at=cutoff,
            )
        )
        return result.rowcount or 0

    async def list_recent_for_room(
        self, *, chatroom_id: uuid.UUID, limit: int
    ) -> Sequence[RecentActivityRow]:
        """Recent submissions in a room (most-recent-first, bounded) for the
        observer context provider — joined to session (subject) and type (key)."""
        rows = (
            await self._db.execute(
                sa.select(
                    _SUB.c.created_at,
                    _SESS.c.subject_user_id,
                    _SUB.c.attempt_no,
                    _TYPE.c.key.label("type_key"),
                    _SUB.c.validation_status,
                    _SUB.c.is_valid,
                    _SUB.c.error_class,
                )
                .select_from(
                    _SUB.join(_SESS, _SESS.c.id == _SUB.c.session_id).join(
                        _TYPE, _TYPE.c.id == _SUB.c.activity_type_id
                    )
                )
                .where(sa.and_(_SUB.c.chatroom_id == chatroom_id, _SUB.c.deleted_at.is_(None)))
                .order_by(_SUB.c.created_at.desc(), _SUB.c.id.desc())
                .limit(limit)
            )
        ).all()
        return [
            RecentActivityRow(
                created_at=r.created_at,
                subject_user_id=r.subject_user_id,
                attempt_no=r.attempt_no,
                type_key=r.type_key,
                validation_status=ValidationStatus(r.validation_status),
                is_valid=r.is_valid,
                error_class=r.error_class,
            )
            for r in rows
        ]

    async def list_filtered(
        self,
        *,
        chatroom_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
        limit: int,
        offset: int,
    ) -> Sequence[ActivitySubmission]:
        """Paginated submissions in a room, optionally narrowed to a session or
        subject (subject via the session join)."""
        stmt = sa.select(*_SUB_COLS).where(
            sa.and_(_SUB.c.chatroom_id == chatroom_id, _SUB.c.deleted_at.is_(None))
        )
        if session_id is not None:
            stmt = stmt.where(_SUB.c.session_id == session_id)
        if subject_user_id is not None:
            stmt = stmt.where(
                _SUB.c.session_id.in_(sa.select(_SESS.c.id).where(_SESS.c.subject_user_id == subject_user_id))
            )
        stmt = stmt.order_by(_SUB.c.created_at.desc(), _SUB.c.id.desc()).limit(limit).offset(offset)
        rows = (await self._db.execute(stmt)).all()
        return [_row_to_submission(r) for r in rows]

    async def aggregate(
        self,
        *,
        chatroom_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
        subject_user_id: uuid.UUID | None = None,
    ) -> ActivityAggregate:
        """Counts, error-class histogram, and latency stats in one grouped query
        (R30.10). The histogram is a correlated ``jsonb_object_agg`` scalar
        subquery over the same filter, keeping it a single round-trip."""
        where = sa.and_(_SUB.c.chatroom_id == chatroom_id, _SUB.c.deleted_at.is_(None))
        if session_id is not None:
            where = sa.and_(where, _SUB.c.session_id == session_id)
        if subject_user_id is not None:
            where = sa.and_(
                where,
                _SUB.c.session_id.in_(
                    sa.select(_SESS.c.id).where(_SESS.c.subject_user_id == subject_user_id)
                ),
            )

        hist_inner = (
            sa.select(_SUB.c.error_class.label("ec"), sa.func.count().label("cnt"))
            .where(sa.and_(where, _SUB.c.error_class.isnot(None)))
            .group_by(_SUB.c.error_class)
            .subquery()
        )
        hist_scalar = (
            sa.select(
                sa.func.coalesce(
                    sa.func.jsonb_object_agg(hist_inner.c.ec, hist_inner.c.cnt),
                    sa.text("'{}'::jsonb"),
                )
            )
            .select_from(hist_inner)
            .scalar_subquery()
        )

        row = (
            await self._db.execute(
                sa.select(
                    sa.func.count().label("total"),
                    sa.func.count().filter(_SUB.c.is_valid.is_(True)).label("valid_count"),
                    sa.func.count()
                    .filter(_SUB.c.validation_status == ValidationStatus.ERROR.value)
                    .label("error_count"),
                    sa.func.count()
                    .filter(_SUB.c.validation_status == ValidationStatus.PENDING.value)
                    .label("pending_count"),
                    sa.func.avg(_SUB.c.latency_ms).label("latency_avg"),
                    sa.func.min(_SUB.c.latency_ms).label("latency_min"),
                    sa.func.max(_SUB.c.latency_ms).label("latency_max"),
                    hist_scalar.label("histogram"),
                ).where(where)
            )
        ).one()

        return ActivityAggregate(
            total=int(row.total or 0),
            valid_count=int(row.valid_count or 0),
            error_count=int(row.error_count or 0),
            pending_count=int(row.pending_count or 0),
            error_class_histogram={str(k): int(v) for k, v in dict(row.histogram or {}).items()},
            latency_avg_ms=float(row.latency_avg) if row.latency_avg is not None else None,
            latency_min_ms=int(row.latency_min) if row.latency_min is not None else None,
            latency_max_ms=int(row.latency_max) if row.latency_max is not None else None,
        )


__all__ = ["ActivitySubmissionRepository"]
