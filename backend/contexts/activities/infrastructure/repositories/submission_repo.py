"""Async repository for ``activity_submissions`` (Chapter §30, R30.01/R30.10).

Owns attempt-count reads, the authoritative insert, the idempotent validation
write-backs (transition only from ``pending``), the observer read join, and the
single-query aggregate. All writes keep the caller's :class:`AsyncSession`.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import (
    FILLED_FIELDS_SUB_SCORE,
    ActivityAggregate,
    ActivitySubmission,
    RecentActivityRow,
    ValidationStatus,
)
from contexts.activities.infrastructure import tables as t
from shared_kernel.db.rowcount import rowcount

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
    _SUB.c.agent_digest,
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
        agent_digest=row.agent_digest,  # type: ignore[attr-defined]
        validated_at=row.validated_at,  # type: ignore[attr-defined]
        deleted_at=row.deleted_at,  # type: ignore[attr-defined]
    )


class ActivitySubmissionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def next_attempt_no(self, session_id: uuid.UUID) -> int:
        """Next ``attempt_no`` for a session: ``max(attempt_no) + 1`` over **all**
        rows (soft-deleted included), so a number is never reused after a
        submission is soft-deleted. Called under the session ``FOR UPDATE`` lock,
        which serializes concurrent submits to the same session."""
        highest = (
            await self._db.execute(
                sa.select(sa.func.coalesce(sa.func.max(_SUB.c.attempt_no), 0)).where(
                    _SUB.c.session_id == session_id
                )
            )
        ).scalar_one()
        return int(highest) + 1

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
        agent_digest: str | None,
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
                agent_digest=agent_digest,
            )
            .returning(_SUB.c.id)
        )
        return uuid.UUID(str(row.scalar_one()))

    async def count_recent_same_error(
        self, *, session_id: uuid.UUID, error_class: str, since: dt.datetime
    ) -> int:
        """Rolling aggregate for the reactive-rules signal (R30.12): submissions in
        this session since ``since`` carrying the same non-null ``error_class``.
        Bounded by the ``(session_id)`` index; best-effort, numeric-only (SEL
        compares int/float). Soft-deleted rows are excluded."""
        count = (
            await self._db.execute(
                sa.select(sa.func.count()).where(
                    sa.and_(
                        _SUB.c.session_id == session_id,
                        _SUB.c.error_class == error_class,
                        _SUB.c.created_at >= since,
                        _SUB.c.deleted_at.is_(None),
                    )
                )
            )
        ).scalar_one()
        return int(count)

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
        agent_digest: str | None = None,
    ) -> bool:
        """Write a completed verdict — idempotent: transitions **only** from
        ``pending``, so a redelivered/retried job or a race with the watchdog is
        a no-op (0 rows). ``agent_digest`` is omitted from the update (leaving the
        submit-time payload-fallback digest in place) unless the caller has a
        richer ``ValidationResult.detail`` to replace it with."""
        values: dict[str, Any] = {
            "validation_status": ValidationStatus.VALIDATED.value,
            "is_valid": is_valid,
            "error_class": error_class,
            "sub_scores": sub_scores,
            "latency_ms": latency_ms,
            "validated_at": validated_at,
        }
        if agent_digest is not None:
            values["agent_digest"] = agent_digest
        result = await self._db.execute(
            _SUB.update()
            .where(
                sa.and_(
                    _SUB.c.id == submission_id,
                    _SUB.c.validation_status == ValidationStatus.PENDING.value,
                )
            )
            .values(**values)
        )
        return bool(rowcount(result))

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
        return bool(rowcount(result))

    async def sweep_stalled(
        self, *, cutoff: dt.datetime, error_class: str, swept_at: dt.datetime, limit: int = 500
    ) -> Sequence[tuple[uuid.UUID, uuid.UUID]]:
        """Watchdog: move ``pending`` rows older than ``cutoff`` to ``error``,
        returning the ``(id, chatroom_id)`` of each transitioned row.

        The identities let the watchdog emit the same per-room ``activity.validated``
        event and ``activity`` workflow signal the completion path emits (F-20) —
        a bare rowcount forecloses that. Bounded per call; the ``pending``-only
        predicate leaves ``validated`` and already-``error`` rows untouched
        (R30.06). ``validated_at`` records when the timeout was actually observed
        (``swept_at``), not the TTL boundary."""
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
                validated_at=swept_at,
            )
            .returning(_SUB.c.id, _SUB.c.chatroom_id)
        )
        return [(row.id, row.chatroom_id) for row in result]

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
                    _SUB.c.agent_digest,
                    _TYPE.c.expose_payload_to_agent,
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
                agent_digest=r.agent_digest,
                expose_payload_to_agent=r.expose_payload_to_agent,
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

    # -- Presentation-block aggregates ([R28.17]) ---------------------------- #

    async def count_field_fills(
        self, *, chatroom_id: uuid.UUID, activity_type_id: uuid.UUID, field_names: Sequence[str]
    ) -> tuple[int, dict[str, int]]:
        """``(submissions counted, {field name: how many answered it})``.

        Only submissions whose ``sub_scores`` carries a ``filled_fields`` **array**
        are in scope, for both the denominator and every tally. A room that adopted
        the coverage validator mid-course therefore reports the population the
        figure is actually about, rather than silently reading every earlier
        submission as "answered nothing".

        One ``count(*) FILTER`` per declared field rather than a set-returning
        function over the array. ``jsonb_array_elements_text`` raises on a value
        that is not an array, and a comma-join SRF is expanded before ``WHERE``
        runs, so the guard could not protect it; ``@>`` is simply false for a
        non-array and needs no guard at all.

        ``@>`` against a one-element array — ``'["home"]'`` — is the containment
        form for "this array holds this string". PostgreSQL-specific, hence the
        ``db``-tier test (backend/CLAUDE.md).
        """
        scoped = sa.and_(
            _SUB.c.chatroom_id == chatroom_id,
            _SUB.c.activity_type_id == activity_type_id,
            _SUB.c.deleted_at.is_(None),
            sa.func.jsonb_typeof(_SUB.c.sub_scores[FILLED_FIELDS_SUB_SCORE]) == "array",
        )
        filled_fields = sa.type_coerce(_SUB.c.sub_scores[FILLED_FIELDS_SUB_SCORE], pg.JSONB)
        columns: list[sa.ColumnElement[Any]] = [sa.func.count().label("submissions")]
        # Positional labels: a property name is owner-authored and may be anything,
        # including something that collides with `submissions` or another field's
        # name after SQL identifier folding.
        columns.extend(
            sa.func.count()
            .filter(filled_fields.contains(sa.cast(sa.literal(json.dumps([name])), pg.JSONB)))
            .label(f"f{i}")
            for i, name in enumerate(field_names)
        )
        row = (await self._db.execute(sa.select(*columns).where(scoped))).one()
        counted = int(row.submissions or 0)
        return counted, {name: int(getattr(row, f"f{i}") or 0) for i, name in enumerate(field_names)}

    async def attempt_summary_rows(
        self,
        *,
        chatroom_id: uuid.UUID,
        activity_type_id: uuid.UUID | None,
        limit: int,
    ) -> tuple[int, list[Any]]:
        """``(submissions in scope, one row per subject)``, newest activity first.

        ``limit + 1`` rows are fetched so the caller can say the listing was cut
        short instead of presenting a truncated table as the whole room.

        Ordering is by the subject's most recent submission, not by attempt count:
        a table sorted by tries reads as a ranking, and this data does not support
        one.
        """
        scoped = sa.and_(_SUB.c.chatroom_id == chatroom_id, _SUB.c.deleted_at.is_(None))
        if activity_type_id is not None:
            scoped = sa.and_(scoped, _SUB.c.activity_type_id == activity_type_id)

        joined = _SUB.join(_SESS, _SESS.c.id == _SUB.c.session_id)
        subject = _SESS.c.subject_user_id
        latest = (
            sa.select(
                subject,
                _SUB.c.validation_status,
                _SUB.c.is_valid,
                _SUB.c.error_class,
                _SUB.c.created_at.label("last_at"),
                sa.func.max(_SUB.c.attempt_no).over(partition_by=subject).label("attempts"),
                sa.func.count().over(partition_by=subject).label("submissions"),
            )
            .select_from(joined)
            .where(scoped)
            # Window functions are evaluated before DISTINCT ON, so `attempts` and
            # `submissions` cover the subject's whole set even though only their
            # newest row survives.
            .distinct(_SESS.c.subject_user_id)
            .order_by(_SESS.c.subject_user_id, _SUB.c.created_at.desc(), _SUB.c.id.desc())
            .subquery()
        )
        rows = (
            await self._db.execute(
                sa.select(latest).order_by(latest.c.last_at.desc(), latest.c.subject_user_id).limit(limit + 1)
            )
        ).all()

        total = (
            await self._db.execute(sa.select(sa.func.count()).select_from(joined).where(scoped))
        ).scalar_one()
        return int(total or 0), list(rows)


__all__ = ["ActivitySubmissionRepository"]
