"""Compiled-SQL invariants for the activities repositories (no DB).

Mirrors ``test_message_repo.py``: mock the ``AsyncSession`` and assert the
statement the repo builds carries the guards its correctness depends on —
``deleted_at IS NULL`` soft-delete filtering, room scoping, the open-session
predicate, and the ``pending``-only validation write-back.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from contexts.activities.infrastructure.repositories.submission_repo import (
    ActivitySubmissionRepository,
)
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository


def _compiled(stmt: object) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})  # type: ignore[attr-defined]
    )


class TestSubmissionRepoScoping:
    async def test_list_recent_for_room_is_scoped_and_bounded(self) -> None:
        room_id = uuid.uuid4()
        db = AsyncMock()
        page = MagicMock()
        page.all.return_value = []
        db.execute.return_value = page

        await ActivitySubmissionRepository(db).list_recent_for_room(chatroom_id=room_id, limit=7)

        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert str(room_id) in compiled
        assert "deleted_at IS NULL" in compiled
        assert "ORDER BY activity_submissions.created_at DESC" in compiled
        assert "LIMIT 7" in compiled
        # Agent-visibility follow-up: the row carries the digest and its type's
        # exposure flag so the context provider can gate per-row.
        assert "activity_submissions.agent_digest" in compiled
        assert "activity_types.expose_payload_to_agent" in compiled

    async def test_record_validation_transitions_only_from_pending(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        db.execute.return_value = result

        await ActivitySubmissionRepository(db).record_validation(
            submission_id=uuid.uuid4(),
            is_valid=True,
            error_class=None,
            sub_scores={},
            latency_ms=12,
            validated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        # JSONB values block literal_binds, so inspect the bound params: the WHERE
        # guard must carry 'pending' and the SET must write 'validated'.
        stmt = db.execute.await_args_list[0].args[0]
        str_values = [
            v for v in stmt.compile(dialect=postgresql.dialect()).params.values() if isinstance(v, str)
        ]
        assert "pending" in str_values
        assert "validated" in str_values

    async def test_record_validation_omits_agent_digest_when_not_given(self) -> None:
        """Agent-visibility follow-up: no ``agent_digest`` kwarg -> the SET clause
        must not touch that column, so the submit-time fallback digest survives."""
        db = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        db.execute.return_value = result

        await ActivitySubmissionRepository(db).record_validation(
            submission_id=uuid.uuid4(),
            is_valid=True,
            error_class=None,
            sub_scores={},
            latency_ms=12,
            validated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "agent_digest" not in sql

    async def test_record_validation_writes_agent_digest_when_given(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        db.execute.return_value = result

        await ActivitySubmissionRepository(db).record_validation(
            submission_id=uuid.uuid4(),
            is_valid=True,
            error_class=None,
            sub_scores={},
            latency_ms=12,
            validated_at=datetime(2026, 1, 1, tzinfo=UTC),
            agent_digest="a rich description",
        )

        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect()))
        assert "agent_digest" in sql
        str_values = [
            v for v in stmt.compile(dialect=postgresql.dialect()).params.values() if isinstance(v, str)
        ]
        assert "a rich description" in str_values

    async def test_sweep_stalled_touches_only_pending(self) -> None:
        db = AsyncMock()
        rows = [SimpleNamespace(id=uuid.uuid4(), chatroom_id=uuid.uuid4()) for _ in range(3)]
        result = MagicMock()
        result.__iter__.return_value = iter(rows)
        db.execute.return_value = result

        swept_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        swept = await ActivitySubmissionRepository(db).sweep_stalled(
            cutoff=datetime(2026, 1, 1, tzinfo=UTC), error_class="stalled", swept_at=swept_at
        )

        assert len(swept) == 3
        compiled = _compiled(db.execute.await_args_list[0].args[0])
        # The pending-only predicate is the must-not-weaken guard: the RETURNING
        # rewrite must not touch the WHERE.
        assert "validation_status = 'pending'" in compiled
        # validated_at records the sweep time, not the (earlier) TTL cutoff.
        assert "2026-01-01 12:00:00" in compiled

    async def test_sweep_stalled_returns_identity_of_each_swept_row(self) -> None:
        db = AsyncMock()
        rows = [SimpleNamespace(id=uuid.uuid4(), chatroom_id=uuid.uuid4()) for _ in range(2)]
        result = MagicMock()
        result.__iter__.return_value = iter(rows)
        db.execute.return_value = result

        swept = await ActivitySubmissionRepository(db).sweep_stalled(
            cutoff=datetime(2026, 1, 1, tzinfo=UTC),
            error_class="stalled",
            swept_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        assert swept == [(r.id, r.chatroom_id) for r in rows]
        # The RETURNING clause must project both columns the watchdog needs to emit.
        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert "RETURNING activity_submissions.id, activity_submissions.chatroom_id" in compiled

    async def test_next_attempt_no_ignores_soft_delete_for_monotonicity(self) -> None:
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one.return_value = 4
        db.execute.return_value = result

        n = await ActivitySubmissionRepository(db).next_attempt_no(uuid.uuid4())

        # max(attempt_no)=4 -> next is 5, and the query must NOT filter deleted_at
        # (else a soft-deleted top attempt would let a number be reused).
        assert n == 5
        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert "max(activity_submissions.attempt_no)" in compiled
        assert "deleted_at" not in compiled


class TestTypeRepoScoping:
    async def test_get_filters_soft_deleted(self) -> None:
        type_id = uuid.uuid4()
        db = AsyncMock()
        row = MagicMock()
        row.first.return_value = SimpleNamespace()  # not reached — get returns None on falsy
        row.first.return_value = None
        db.execute.return_value = row

        await ActivityTypeRepository(db).get(type_id)

        compiled = _compiled(db.execute.await_args_list[0].args[0])
        assert str(type_id) in compiled
        assert "deleted_at IS NULL" in compiled
