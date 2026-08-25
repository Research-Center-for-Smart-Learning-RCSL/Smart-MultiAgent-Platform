"""The presentation-block aggregates: shaping, ordering and refusals ([R28.17]).

The SQL these build is PostgreSQL-specific and its *behaviour* is pinned by
``tests/integration/test_observation_aggregates_db.py`` — the unit tier compiles
with ``literal_binds`` and never executes, so it can only see statement text
(``backend/CLAUDE.md``). What lives here is everything that is decided in Python:
the declared-field ordering, the width bound, the ``None`` refusals, and the
promise that a read model carries codes and counts rather than ids or answers
([R28.18]).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from contexts.activities.application.observation_aggregates import (
    ObservationAggregateService,
    declared_fields,
)
from contexts.activities.domain.models import (
    MAX_COVERAGE_FIELDS,
    ActivityType,
    ActivityTypeScope,
    ValidatorKind,
)
from contexts.activities.infrastructure.repositories.submission_repo import (
    ActivitySubmissionRepository,
)

_ROOM = uuid.UUID("11111111-1111-1111-1111-111111111111")
_SUBJECT = uuid.UUID("abcdef12-3456-7890-abcd-ef1234567890")


def _type(properties: dict[str, Any]) -> ActivityType:
    return ActivityType(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        project_id=uuid.uuid4(),
        key="mandala-9grid",
        name="Mandala",
        payload_schema={"type": "object", "properties": properties},
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={},
        retention_days=None,
        version=1,
        created_at=dt.datetime(2026, 8, 24, tzinfo=dt.UTC),
        scope=ActivityTypeScope.PROJECT,
    )


class _FakeRepo:
    """Stands in for the repository so the service's own decisions are visible."""

    def __init__(self, *, counted: int = 0, tallies: dict[str, int] | None = None, rows: list = ()):
        self.counted = counted
        self.tallies = tallies or {}
        self.rows = list(rows)
        self.field_names: list[str] = []

    async def count_field_fills(self, *, chatroom_id, activity_type_id, field_names):
        self.field_names = list(field_names)
        return self.counted, self.tallies

    async def attempt_summary_rows(self, *, chatroom_id, activity_type_id, limit):
        return self.counted, self.rows[: limit + 1]


def _service(repo: _FakeRepo) -> ObservationAggregateService:
    service = ObservationAggregateService.__new__(ObservationAggregateService)
    service._repo = repo  # type: ignore[attr-defined]
    return service


def _attempt_row(*, attempts: int, submissions: int, status: str = "validated", is_valid: bool = True):
    from types import SimpleNamespace

    return SimpleNamespace(
        subject_user_id=_SUBJECT,
        validation_status=status,
        is_valid=is_valid,
        error_class=None,
        attempts=attempts,
        submissions=submissions,
    )


class TestDeclaredFields:
    def test_x_order_wins_over_declaration_order(self) -> None:
        fields = declared_fields(
            {
                "properties": {
                    "b": {"x-order": 2, "title": "B"},
                    "a": {"x-order": 1, "title": "A"},
                }
            }
        )
        assert fields == [("a", "A"), ("b", "B")]

    def test_properties_without_x_order_keep_declaration_order_behind_those_with_one(self) -> None:
        fields = declared_fields({"properties": {"z": {}, "y": {}, "ordered": {"x-order": 3}}})
        assert [name for name, _ in fields] == ["ordered", "z", "y"]

    def test_the_title_falls_back_to_the_property_name(self) -> None:
        assert declared_fields({"properties": {"home": {"type": "string"}}}) == [("home", "home")]
        assert declared_fields({"properties": {"home": {"title": ""}}}) == [("home", "home")]

    def test_a_boolean_x_order_is_not_read_as_a_position(self) -> None:
        """`True` is an `int` in Python, so an unguarded check would sort it as 1."""
        fields = declared_fields({"properties": {"flagged": {"x-order": True}, "first": {"x-order": 1}}})
        assert [name for name, _ in fields] == ["first", "flagged"]

    @pytest.mark.parametrize(
        "schema",
        [{}, {"properties": {}}, {"properties": []}, {"properties": None}],
        ids=["no-properties", "empty", "not-a-dict", "null"],
    )
    def test_a_schema_with_nothing_to_count_yields_nothing(self, schema: dict[str, Any]) -> None:
        assert declared_fields(schema) == []

    def test_a_schema_wider_than_the_bound_yields_nothing(self) -> None:
        """The query builds one aggregate per field, so the bound is on the
        statement rather than only on the picture."""
        wide: dict[str, Any] = {f"f{i}": {} for i in range(MAX_COVERAGE_FIELDS + 1)}
        assert declared_fields({"properties": wide}) == []
        at_bound: dict[str, Any] = {f"f{i}": {} for i in range(MAX_COVERAGE_FIELDS)}
        assert len(declared_fields({"properties": at_bound})) == MAX_COVERAGE_FIELDS


class TestFieldCoverageShaping:
    async def test_cells_follow_declared_order_and_default_to_zero(self) -> None:
        repo = _FakeRepo(counted=4, tallies={"b": 3})
        coverage = await _service(repo).field_coverage(
            chatroom_id=_ROOM, activity_type=_type({"a": {"x-order": 1}, "b": {"x-order": 2}})
        )
        assert coverage is not None
        assert [(c.name, c.filled) for c in coverage.cells] == [("a", 0), ("b", 3)]
        assert coverage.submissions_counted == 4
        assert repo.field_names == ["a", "b"]

    async def test_no_submission_carrying_the_key_refuses_rather_than_renders_zero(self) -> None:
        """AC-6. An all-zero chart asserts nobody answered anything, which is a
        different claim from "this room has no coverage data"."""
        assert (
            await _service(_FakeRepo(counted=0)).field_coverage(
                chatroom_id=_ROOM, activity_type=_type({"a": {}})
            )
        ) is None

    async def test_a_type_with_no_declared_properties_is_refused_without_a_query(self) -> None:
        repo = _FakeRepo(counted=9)
        assert (await _service(repo).field_coverage(chatroom_id=_ROOM, activity_type=_type({}))) is None
        assert repo.field_names == []


class TestMandalaGrid:
    async def test_nine_properties_become_three_rows_of_three(self) -> None:
        props = {f"c{i}": {"x-order": i} for i in range(1, 10)}
        grid = await _service(_FakeRepo(counted=2, tallies={"c5": 2})).mandala_grid(
            chatroom_id=_ROOM, activity_type=_type(props)
        )
        assert grid is not None
        assert [len(r) for r in grid.rows] == [3, 3, 3]
        assert grid.rows[1][1].name == "c5"  # the centre cell
        assert grid.rows[1][1].filled == 2

    @pytest.mark.parametrize("width", [8, 10], ids=["too-narrow", "too-wide"])
    async def test_any_other_width_is_refused_rather_than_padded(self, width: int) -> None:
        props = {f"c{i}": {"x-order": i} for i in range(1, width + 1)}
        assert (
            await _service(_FakeRepo(counted=1)).mandala_grid(chatroom_id=_ROOM, activity_type=_type(props))
        ) is None


class TestAttemptSummaryShaping:
    async def test_rows_carry_codes_and_the_denominator_is_submissions(self) -> None:
        summary = await _service(
            _FakeRepo(counted=5, rows=[_attempt_row(attempts=3, submissions=5)])
        ).attempt_summary(chatroom_id=_ROOM, activity_type=None, limit=30)
        assert summary is not None
        assert summary.rows[0].subject_code == "u:abcdef12"
        assert summary.rows[0].attempts == 3
        assert summary.submissions_counted == 5
        assert summary.truncated is False
        assert str(_SUBJECT) not in repr(summary)

    async def test_an_over_limit_row_is_dropped_and_the_cut_is_reported(self) -> None:
        rows = [_attempt_row(attempts=1, submissions=1) for _ in range(3)]
        summary = await _service(_FakeRepo(counted=3, rows=rows)).attempt_summary(
            chatroom_id=_ROOM, activity_type=None, limit=2
        )
        assert summary is not None
        assert len(summary.rows) == 2
        assert summary.truncated is True

    @pytest.mark.parametrize(
        ("status", "is_valid", "expected"),
        [
            ("validated", True, "valid"),
            ("validated", False, "invalid"),
            ("pending", None, "pending"),
            ("error", None, "error"),
        ],
    )
    async def test_outcomes_use_the_context_block_s_own_four_words(
        self, status: str, is_valid: bool | None, expected: str
    ) -> None:
        summary = await _service(
            _FakeRepo(
                counted=1,
                rows=[_attempt_row(attempts=1, submissions=1, status=status, is_valid=is_valid)],
            )
        ).attempt_summary(chatroom_id=_ROOM, activity_type=None, limit=30)
        assert summary is not None
        assert summary.rows[0].latest_outcome == expected

    async def test_an_empty_room_refuses_rather_than_rendering_an_empty_table(self) -> None:
        assert (
            await _service(_FakeRepo(counted=0)).attempt_summary(
                chatroom_id=_ROOM, activity_type=None, limit=30
            )
        ) is None


class TestCompiledSql:
    """Statement text only. What these queries *do* is pinned by the db tier."""

    @staticmethod
    def _sql(statement) -> str:
        return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    async def test_coverage_counts_are_scoped_and_use_containment_not_an_srf(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        db = AsyncMock()
        result = MagicMock()
        result.one.return_value = MagicMock(submissions=0)
        db.execute.return_value = result
        await ActivitySubmissionRepository(db).count_field_fills(
            chatroom_id=_ROOM, activity_type_id=_ROOM, field_names=["home"]
        )
        sql = self._sql(db.execute.await_args_list[0].args[0])
        assert str(_ROOM) in sql
        assert "deleted_at IS NULL" in sql
        assert "jsonb_typeof" in sql
        assert "@>" in sql
        assert "FILTER" in sql
        # A set-returning function would raise on a non-array value, and the
        # guard cannot protect it — a comma-join SRF is expanded before WHERE.
        assert "jsonb_array_elements" not in sql

    async def test_attempt_rows_are_room_scoped_and_ask_for_one_more_than_the_limit(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        db = AsyncMock()
        rows_result, count_result = MagicMock(), MagicMock()
        rows_result.all.return_value = []
        count_result.scalar_one.return_value = 0
        db.execute.side_effect = [rows_result, count_result]
        await ActivitySubmissionRepository(db).attempt_summary_rows(
            chatroom_id=_ROOM, activity_type_id=None, limit=5
        )
        sql = self._sql(db.execute.await_args_list[0].args[0])
        assert str(_ROOM) in sql
        assert "deleted_at IS NULL" in sql
        assert "DISTINCT ON" in sql
        assert "LIMIT 6" in sql
