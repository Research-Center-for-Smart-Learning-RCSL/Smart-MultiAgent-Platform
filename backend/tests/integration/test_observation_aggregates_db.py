"""The presentation-block aggregates against a real PostgreSQL (AC-6, [R28.17]).

WHY THIS CANNOT BE A UNIT TEST
------------------------------
Both queries are written in PostgreSQL-specific terms — ``jsonb_typeof``, the
``@>`` containment operator against a ``::jsonb`` cast, ``count(*) FILTER`` and
``DISTINCT ON`` beside window functions. The unit tier compiles with
``literal_binds`` and never executes, so it can only see SQL text that *would*
work if pasted into psql (``backend/CLAUDE.md``). Three things here are invisible
there and each would ship as a 500 on every observer turn:

- that ``@>`` is **false**, not an error, for a submission whose ``sub_scores``
  carries no ``filled_fields`` — the mid-course upgrade case the whole design
  turns on;
- that window functions are evaluated before ``DISTINCT ON``, so each subject's
  ``attempts`` covers their whole set rather than only their newest row;
- that a ``jsonb`` cast of a bound text parameter resolves at all.

The privacy assertions ([R28.18]) live here for the same reason: they are only
meaningful over data that actually round-tripped through the column.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.application.observation_aggregates import ObservationAggregateService
from contexts.activities.domain.models import ActivityType, ActivityTypeScope, ValidatorKind
from contexts.activities.infrastructure import tables as at
from contexts.conversation.infrastructure import tables as t
from contexts.identity.infrastructure.tables import users as users_t

pytestmark = pytest.mark.db

#: Every submission's payload is this, in every field. A value that reaches an
#: aggregate shows up as itself in the failure message.
_ANSWER = "PARTICIPANT-PROSE-DO-NOT-LEAK"
_NINE = [f"cell_{i}" for i in range(1, 10)]
_DELETED_AT = dt.datetime(2026, 8, 24, tzinfo=dt.UTC)


def _schema(names: list[str]) -> dict[str, object]:
    """Descending ``x-order``, so declared order and rendered order differ and a
    test asserting the order is asserting the rule rather than dict insertion."""
    return {
        "type": "object",
        "properties": {
            name: {"type": "string", "title": f"T-{name}", "x-order": len(names) - i}
            for i, name in enumerate(names)
        },
    }


@dataclass(frozen=True)
class _Fixture:
    chatroom_id: uuid.UUID
    activity_type: ActivityType
    subjects: tuple[uuid.UUID, ...]
    session_ids: dict[uuid.UUID, uuid.UUID]


@pytest.fixture
async def fixture(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[_Fixture]:
    """A room, a nine-cell type, one round, and two subjects with a session each."""
    project_id, user_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id = uuid.uuid4(), uuid.uuid4()
    second_user = uuid.uuid4()
    key = f"obsagg-{uuid.uuid4().hex[:8]}"
    schema = _schema(_NINE)
    subjects = (user_id, second_user)
    session_ids = {subject: uuid.uuid4() for subject in subjects}

    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="obsagg-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="obsagg-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            users_t.insert().values(
                id=second_user, email=f"obsagg-{second_user}@test.invalid", password_hash="x"
            )
        )
        await session.execute(
            at.activity_types.insert().values(
                id=type_id,
                project_id=project_id,
                key=key,
                name="Mandala itest",
                payload_schema=schema,
                validator_kind=ValidatorKind.IN_PROCESS.value,
                validator_config={"validator_id": "filled_count_coverage", "min_filled": 1},
                retention_days=None,
                expose_payload_to_agent=True,
                echo_includes_content=False,
            )
        )
        await session.execute(
            at.activity_activations.insert().values(
                id=activation_id,
                chatroom_id=chatroom_id,
                activity_type_id=type_id,
                started_by_user_id=user_id,
            )
        )
        for subject, session_id in session_ids.items():
            await session.execute(
                at.activity_sessions.insert().values(
                    id=session_id,
                    activity_type_id=type_id,
                    chatroom_id=chatroom_id,
                    subject_user_id=subject,
                    activation_id=activation_id,
                )
            )
        await session.commit()

    activity_type = ActivityType(
        id=type_id,
        project_id=project_id,
        key=key,
        name="Mandala itest",
        payload_schema=schema,
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count_coverage", "min_filled": 1},
        retention_days=None,
        version=1,
        created_at=_DELETED_AT,
        scope=ActivityTypeScope.PROJECT,
    )
    try:
        yield _Fixture(chatroom_id, activity_type, subjects, session_ids)
    finally:
        # The room, type, round and sessions ride the project cascade; the extra
        # user does not belong to it.
        async with sessionmaker() as cleanup:
            await cleanup.execute(users_t.delete().where(users_t.c.id == second_user))
            await cleanup.commit()


async def _submit(
    sessionmaker: async_sessionmaker[AsyncSession],
    fx: _Fixture,
    *,
    subject: uuid.UUID,
    attempt_no: int,
    sub_scores: dict[str, object],
    is_valid: bool = True,
    error_class: str | None = None,
    deleted: bool = False,
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            at.activity_submissions.insert().values(
                session_id=fx.session_ids[subject],
                activity_type_id=fx.activity_type.id,
                chatroom_id=fx.chatroom_id,
                producer_user_id=subject,
                payload=dict.fromkeys(_NINE, _ANSWER),
                attempt_no=attempt_no,
                validation_status="validated",
                is_valid=is_valid,
                error_class=error_class,
                sub_scores=sub_scores,
                agent_digest="computed digest",
                deleted_at=_DELETED_AT if deleted else None,
            )
        )
        await session.commit()


class TestFieldCoverage:
    async def test_counts_only_submissions_carrying_filled_fields(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        """The mid-course upgrade case. A `filled_count` submission carries no
        `filled_fields` key at all: `@>` against a missing key must be false rather
        than raise, and the row must stay out of the denominator so the figure
        reports the population it is actually about."""
        await _submit(
            sessionmaker,
            fixture,
            subject=fixture.subjects[0],
            attempt_no=1,
            sub_scores={"filled": 9},
        )
        await _submit(
            sessionmaker,
            fixture,
            subject=fixture.subjects[1],
            attempt_no=1,
            sub_scores={"filled": 2, "filled_fields": ["cell_1", "cell_3"]},
        )
        async with sessionmaker() as session:
            coverage = await ObservationAggregateService(session).field_coverage(
                chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type
            )
        assert coverage is not None
        assert coverage.submissions_counted == 1
        by_name = {c.name: c.filled for c in coverage.cells}
        assert by_name["cell_1"] == 1
        assert by_name["cell_3"] == 1
        assert by_name["cell_2"] == 0

    async def test_returns_none_when_nothing_carries_the_key(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        """AC-6's refusal half: an empty chart would assert nobody answered."""
        await _submit(
            sessionmaker, fixture, subject=fixture.subjects[0], attempt_no=1, sub_scores={"filled": 4}
        )
        async with sessionmaker() as session:
            assert (
                await ObservationAggregateService(session).field_coverage(
                    chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type
                )
            ) is None

    async def test_soft_deleted_submissions_are_excluded(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        await _submit(
            sessionmaker,
            fixture,
            subject=fixture.subjects[0],
            attempt_no=1,
            sub_scores={"filled_fields": ["cell_1"]},
            deleted=True,
        )
        async with sessionmaker() as session:
            assert (
                await ObservationAggregateService(session).field_coverage(
                    chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type
                )
            ) is None

    async def test_no_payload_value_reaches_the_aggregate(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        """AC-7's coverage half."""
        await _submit(
            sessionmaker,
            fixture,
            subject=fixture.subjects[0],
            attempt_no=1,
            sub_scores={"filled_fields": ["cell_1"]},
        )
        async with sessionmaker() as session:
            coverage = await ObservationAggregateService(session).field_coverage(
                chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type
            )
        assert coverage is not None
        assert _ANSWER not in repr(coverage)

    async def test_mandala_grid_is_three_rows_of_three_in_x_order(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        await _submit(
            sessionmaker,
            fixture,
            subject=fixture.subjects[0],
            attempt_no=1,
            sub_scores={"filled_fields": ["cell_9"]},
        )
        async with sessionmaker() as session:
            grid = await ObservationAggregateService(session).mandala_grid(
                chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type
            )
        assert grid is not None
        assert [len(r) for r in grid.rows] == [3, 3, 3]
        # `_schema` assigns descending x-order, so cell_9 sorts first.
        assert grid.rows[0][0].name == "cell_9"
        assert grid.rows[0][0].filled == 1

    async def test_mandala_grid_refuses_a_type_that_is_not_nine_wide(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        await _submit(
            sessionmaker,
            fixture,
            subject=fixture.subjects[0],
            attempt_no=1,
            sub_scores={"filled_fields": ["cell_1"]},
        )
        narrow = replace(fixture.activity_type, payload_schema=_schema(_NINE[:4]))
        async with sessionmaker() as session:
            assert (
                await ObservationAggregateService(session).mandala_grid(
                    chatroom_id=fixture.chatroom_id, activity_type=narrow
                )
            ) is None


class TestAttemptSummary:
    async def test_attempts_cover_the_whole_set_not_just_the_newest_row(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        """The window-before-DISTINCT-ON property. The last row inserted is attempt
        2, not the high-water mark, so reading `attempts` off the newest row alone
        would report 2 and this would fail."""
        subject = fixture.subjects[0]
        for attempt in (1, 3, 2):
            await _submit(
                sessionmaker,
                fixture,
                subject=subject,
                attempt_no=attempt,
                sub_scores={"filled_fields": ["cell_1"]},
                is_valid=attempt != 2,
                error_class=None if attempt != 2 else "too_few_filled",
            )
        async with sessionmaker() as session:
            summary = await ObservationAggregateService(session).attempt_summary(
                chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type, limit=30
            )
        assert summary is not None
        assert len(summary.rows) == 1
        row = summary.rows[0]
        assert row.attempts == 3
        assert row.submissions == 3
        assert row.latest_outcome == "invalid"
        assert row.latest_error_class == "too_few_filled"

    async def test_rows_are_codes_and_never_ids_or_answers(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        """AC-7. The full UUID must not survive into the read model."""
        subject = fixture.subjects[0]
        await _submit(sessionmaker, fixture, subject=subject, attempt_no=1, sub_scores={"filled_fields": []})
        async with sessionmaker() as session:
            summary = await ObservationAggregateService(session).attempt_summary(
                chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type, limit=30
            )
        assert summary is not None
        assert summary.rows[0].subject_code == f"u:{str(subject)[:8]}"
        assert str(subject) not in repr(summary)
        assert _ANSWER not in repr(summary)

    async def test_the_limit_reports_truncation_rather_than_hiding_it(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        for subject in fixture.subjects:
            await _submit(
                sessionmaker, fixture, subject=subject, attempt_no=1, sub_scores={"filled_fields": []}
            )
        async with sessionmaker() as session:
            summary = await ObservationAggregateService(session).attempt_summary(
                chatroom_id=fixture.chatroom_id, activity_type=fixture.activity_type, limit=1
            )
        assert summary is not None
        assert len(summary.rows) == 1
        assert summary.truncated is True
        # The denominator still counts everything in scope, not just what fit.
        assert summary.submissions_counted == 2

    async def test_returns_none_for_a_room_with_no_submissions(
        self, sessionmaker: async_sessionmaker[AsyncSession], fixture: _Fixture
    ) -> None:
        async with sessionmaker() as session:
            assert (
                await ObservationAggregateService(session).attempt_summary(
                    chatroom_id=fixture.chatroom_id, activity_type=None, limit=30
                )
            ) is None
