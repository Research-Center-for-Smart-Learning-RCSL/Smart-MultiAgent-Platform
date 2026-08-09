"""`smap.examples` seeder: idempotency, the seeded definitions, and CLI wiring.

Pins AC-10 (two runs, second one a no-op) and AC-11 (the visibility/retention
settings and schema well-formedness of both seeded types).
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from app.plugins.activity_validators import register_first_party_validators
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.application.validators import registry
from contexts.activities.application.validators.schema import (
    payload_errors,
    validate_schema_wellformed,
)
from contexts.activities.domain.errors import ValidatorConfigInvalid
from contexts.activities.domain.models import ValidatorKind
from smap.examples import _seeding
from smap.examples._catalogue import CourseActivityType, load_course

COURSE_TYPES = load_course("creative-thinking").activity_types


class _FakeSession:
    def __init__(self) -> None:
        self.commit = AsyncMock()


def _patch_infra(monkeypatch: pytest.MonkeyPatch, facade: MagicMock) -> _FakeSession:
    """Route the seeder at a fake session + facade without touching a DB."""
    session = _FakeSession()

    @asynccontextmanager
    async def _sessionmaker_call() -> Any:
        yield session

    monkeypatch.setattr(_seeding, "get_sessionmaker", lambda: _sessionmaker_call)
    monkeypatch.setattr(_seeding, "ActivitiesFacade", lambda _s: facade)
    return session


async def _seed_the_course(project_id: uuid.UUID, owner_user_id: uuid.UUID) -> _seeding.SeedReport:
    return await _seeding.seed_course(
        project_id=project_id,
        owner_user_id=owner_user_id,
        activity_types=COURSE_TYPES,
    )


def _facade(existing_keys: list[str]) -> MagicMock:
    facade = MagicMock()
    facade.list_types = AsyncMock(return_value=[MagicMock(key=k) for k in existing_keys])
    facade.register_type = AsyncMock()
    return facade


class TestSeededDefinitions:
    def test_seeds_exactly_the_two_units(self) -> None:
        assert [t.key for t in COURSE_TYPES] == [
            "mandala-9grid",
            "six-hats-emotion-desk",
        ]

    @pytest.mark.parametrize("course_type", COURSE_TYPES, ids=lambda t: t.key)
    def test_payload_schema_is_wellformed(self, course_type: CourseActivityType) -> None:
        validate_schema_wellformed(course_type.payload_schema)

    @pytest.mark.parametrize("course_type", COURSE_TYPES, ids=lambda t: t.key)
    def test_visibility_and_retention_settings(self, course_type: CourseActivityType) -> None:
        # Q-5: agents read the digest, the room transcript does not echo content.
        assert course_type.expose_payload_to_agent is True
        assert course_type.echo_includes_content is False
        # Q-6: retention is the researcher's IRB call, not a seeded default.
        assert course_type.retention_days is None

    @pytest.mark.parametrize("course_type", COURSE_TYPES, ids=lambda t: t.key)
    def test_uses_filled_count_with_a_valid_config(self, course_type: CourseActivityType) -> None:
        from app.plugins.activity_validators import validate_filled_count_config

        assert course_type.validator_kind is ValidatorKind.IN_PROCESS
        assert course_type.validator_config["validator_id"] == "filled_count"
        validate_filled_count_config(course_type.validator_config)

    def test_mandala_is_a_nine_field_schema_with_a_center(self) -> None:
        """The bundled plugin lays out 3x3 only for nine fields including `center`."""
        schema = COURSE_TYPES[0].payload_schema
        properties = schema["properties"]
        assert len(properties) == 9
        assert "center" in properties
        assert list(properties) == ["center", *[f"cell_{i}" for i in range(1, 9)]]

    def test_six_hats_covers_the_five_hats_plus_the_event(self) -> None:
        properties = COURSE_TYPES[1].payload_schema["properties"]
        assert set(properties) == {
            "event",
            "hat_white",
            "hat_red",
            "hat_yellow",
            "hat_black",
            "hat_blue",
        }

    @pytest.mark.parametrize("course_type", COURSE_TYPES, ids=lambda t: t.key)
    def test_a_realistic_submission_passes_schema_validation(self, course_type: CourseActivityType) -> None:
        payload = {name: "x" for name in course_type.payload_schema["properties"]}
        assert payload_errors(course_type.payload_schema, payload) == []


class TestSeededConfigsPassTheRealRegistrationGate:
    """The gate `register_type` actually applies, with the registry as the CLI leaves it.

    The idempotency tests below replace `ActivitiesFacade` with a mock, so they
    never exercise `_validate_validator_config` — which is how a seeder that failed
    on every real run shipped green. These tests use the real service method and
    start from an empty registry, exactly like a fresh CLI process.
    """

    def teardown_method(self) -> None:
        registry.clear_registry()

    @pytest.mark.parametrize("course_type", COURSE_TYPES, ids=lambda t: t.key)
    def test_config_rejected_when_no_registration_site_has_run(self, course_type: CourseActivityType) -> None:
        """Pins *why* the seeder must register: a bare CLI process has an empty registry."""
        registry.clear_registry()
        with pytest.raises(ValidatorConfigInvalid):
            ActivityTypeService._validate_validator_config(
                course_type.validator_kind, course_type.validator_config
            )

    @pytest.mark.parametrize("course_type", COURSE_TYPES, ids=lambda t: t.key)
    def test_config_accepted_after_the_seeder_registers(self, course_type: CourseActivityType) -> None:
        registry.clear_registry()
        register_first_party_validators()
        ActivityTypeService._validate_validator_config(
            course_type.validator_kind, course_type.validator_config
        )

    async def test_seed_registers_validators_before_touching_the_facade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The registration must happen inside `seed_course`, not merely at module import.

        Importing the seeder module pulls in `app.plugins.activity_validators`, whose
        module scope registers as a side effect — so an import-only guarantee would
        pass this file while a `clear_registry()` anywhere upstream still broke the
        real run.
        """
        registry.clear_registry()
        facade = _facade(existing_keys=[])
        _patch_infra(monkeypatch, facade)

        await _seed_the_course(uuid.uuid4(), uuid.uuid4())

        assert registry.is_registered("filled_count")


class TestSeederIdempotency:
    async def test_first_run_registers_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = _facade(existing_keys=[])
        session = _patch_infra(monkeypatch, facade)

        report = await _seed_the_course(uuid.uuid4(), uuid.uuid4())

        assert report.created == ["mandala-9grid", "six-hats-emotion-desk"]
        assert report.already_present == []
        assert facade.register_type.await_count == 2
        session.commit.assert_awaited_once()

    async def test_second_run_registers_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = _facade(existing_keys=["mandala-9grid", "six-hats-emotion-desk"])
        _patch_infra(monkeypatch, facade)

        report = await _seed_the_course(uuid.uuid4(), uuid.uuid4())

        assert report.created == []
        assert report.already_present == ["mandala-9grid", "six-hats-emotion-desk"]
        facade.register_type.assert_not_awaited()

    async def test_partial_run_fills_only_the_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = _facade(existing_keys=["mandala-9grid"])
        _patch_infra(monkeypatch, facade)

        report = await _seed_the_course(uuid.uuid4(), uuid.uuid4())

        assert report.created == ["six-hats-emotion-desk"]
        assert report.already_present == ["mandala-9grid"]

    async def test_registers_with_the_operator_supplied_audit_actor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facade = _facade(existing_keys=[])
        _patch_infra(monkeypatch, facade)
        project_id, owner_id = uuid.uuid4(), uuid.uuid4()

        await _seed_the_course(project_id, owner_id)

        kwargs = facade.register_type.await_args_list[0].kwargs
        assert kwargs["project_id"] == project_id
        assert kwargs["actor_user_id"] == owner_id
        assert kwargs["actor_ip"] is None


class TestRunReturnsTheReport:
    """G-2: `run()`'s return shape, which the command formats its output from."""

    def test_run_returns_the_seed_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = _facade(existing_keys=["mandala-9grid"])
        _patch_infra(monkeypatch, facade)

        report = _seeding.run(
            project_id=uuid.uuid4(),
            owner_user_id=uuid.uuid4(),
            activity_types=COURSE_TYPES,
        )

        assert isinstance(report, _seeding.SeedReport)
        assert report.created == ["six-hats-emotion-desk"]
        assert report.already_present == ["mandala-9grid"]


class TestAddingACourseNeedsNoCode:
    """AC-7: a course dropped into a directory seeds like any other.

    Nothing here imports a course-specific symbol, which is the property the
    refactor exists to establish.
    """

    async def test_a_fixture_course_seeds_through_the_same_engine(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "extra-course.json").write_bytes(
            json.dumps(
                {
                    "course_key": "extra-course",
                    "title": "A course added without touching Python",
                    "source": "test fixture",
                    "activity_types": [
                        {
                            "key": "brand-new-unit",
                            "name": "全新單元",
                            "validator_kind": "in_process",
                            "validator_config": {"validator_id": "filled_count", "min_filled": 1},
                            "retention_days": 30,
                            "expose_payload_to_agent": False,
                            "echo_includes_content": True,
                            "payload_schema": {
                                "type": "object",
                                "properties": {"answer": {"type": "string", "title": "答案"}},
                                "required": ["answer"],
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        facade = _facade(existing_keys=[])
        _patch_infra(monkeypatch, facade)
        course = load_course("extra-course", root=tmp_path)

        report = await _seeding.seed_course(
            project_id=uuid.uuid4(),
            owner_user_id=uuid.uuid4(),
            activity_types=course.activity_types,
        )

        assert report.created == ["brand-new-unit"]
        kwargs = facade.register_type.await_args_list[0].kwargs
        assert kwargs["key"] == "brand-new-unit"
        assert kwargs["name"] == "全新單元"
        # The per-course flags reach the facade rather than being fixed in code.
        assert kwargs["retention_days"] == 30
        assert kwargs["expose_payload_to_agent"] is False
        assert kwargs["echo_includes_content"] is True


class TestSeederCli:
    def test_reports_created_and_already_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The operator's only feedback that a re-run was a no-op rather than a failure."""
        from smap.examples import __main__ as cli

        report = _seeding.SeedReport(created=["six-hats-emotion-desk"], already_present=["mandala-9grid"])
        monkeypatch.setattr(cli, "run_seed", MagicMock(return_value=report))
        recorded = MagicMock()
        monkeypatch.setattr(cli, "logger", recorded)

        result = CliRunner().invoke(
            cli.app,
            [
                "creative-thinking-course",
                "--project-id",
                str(uuid.uuid4()),
                "--owner-user-id",
                str(uuid.uuid4()),
            ],
        )

        assert result.exit_code == 0, result.output
        args = recorded.info.call_args.args
        assert args[1] == "creative-thinking"
        assert args[2] == ["six-hats-emotion-desk"]
        assert args[3] == ["mandala-9grid"]

    def test_seeds_the_creative_thinking_course_without_a_course_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-3: the documented invocation gains no required flag."""
        from smap.examples import __main__ as cli

        seeded = MagicMock(return_value=_seeding.SeedReport())
        monkeypatch.setattr(cli, "run_seed", seeded)

        result = CliRunner().invoke(
            cli.app,
            [
                "creative-thinking-course",
                "--project-id",
                str(uuid.uuid4()),
                "--owner-user-id",
                str(uuid.uuid4()),
            ],
        )

        assert result.exit_code == 0, result.output
        assert [t.key for t in seeded.call_args.kwargs["activity_types"]] == [
            "mandala-9grid",
            "six-hats-emotion-desk",
        ]

    def test_exits_1_on_an_unknown_course(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from smap.examples import __main__ as cli

        seeded = MagicMock()
        monkeypatch.setattr(cli, "run_seed", seeded)

        result = CliRunner().invoke(
            cli.app,
            [
                "creative-thinking-course",
                "--project-id",
                str(uuid.uuid4()),
                "--owner-user-id",
                str(uuid.uuid4()),
                "--course",
                "no-such-course",
            ],
        )

        assert result.exit_code == 1
        # Nothing may reach the database once the course failed to load.
        seeded.assert_not_called()

    def test_exits_1_when_loading_fails_unexpectedly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The loader is fed hand-edited data; no input to it may crash the CLI."""
        from smap.examples import __main__ as cli

        seeded = MagicMock()
        monkeypatch.setattr(cli, "run_seed", seeded)
        monkeypatch.setattr(cli, "load_course", MagicMock(side_effect=RecursionError("too deep")))

        result = CliRunner().invoke(
            cli.app,
            [
                "creative-thinking-course",
                "--project-id",
                str(uuid.uuid4()),
                "--owner-user-id",
                str(uuid.uuid4()),
            ],
        )

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        seeded.assert_not_called()

    def test_help_renders_and_exposes_the_subcommand(self) -> None:
        from smap.examples.__main__ import app

        result = CliRunner().invoke(app, ["--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output
        # The callback must keep group mode, or the documented invocation breaks.
        assert "creative-thinking-course" in result.output

    def test_rejects_a_malformed_uuid_without_a_traceback(self) -> None:
        from smap.examples.__main__ import app

        result = CliRunner().invoke(
            app,
            ["creative-thinking-course", "--project-id", "nope", "--owner-user-id", str(uuid.uuid4())],
        )
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_exits_1_when_seeding_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from smap.examples import __main__ as cli

        monkeypatch.setattr(cli, "run_seed", MagicMock(side_effect=RuntimeError("db down")))
        result = CliRunner().invoke(
            cli.app,
            [
                "creative-thinking-course",
                "--project-id",
                str(uuid.uuid4()),
                "--owner-user-id",
                str(uuid.uuid4()),
            ],
        )
        assert result.exit_code == 1
