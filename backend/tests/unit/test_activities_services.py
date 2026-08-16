"""Unit tests for the activities validators + services (AC-1, AC-2, AC-3).

DB is mocked (repo instances replaced): these pin schema validation, the
in-process scoring path, server-authoritative scoring (client score ignored),
attempt numbering, and validator-config rejection — no Postgres required.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application import session_service as sess_svc
from contexts.activities.application import submission_service as ss
from contexts.activities.application.session_service import ActivitySessionService
from contexts.activities.application.submission_service import SubmissionService
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.application.validators import registry
from contexts.activities.application.validators.schema import (
    payload_errors,
    validate_schema_wellformed,
)
from contexts.activities.domain.errors import (
    ActivityNotActive,
    ActivityTypeNotFound,
    PayloadSchemaInvalid,
    SessionNotFound,
    SubmissionPayloadInvalid,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    ActivityTypeScope,
    SessionStatus,
    ValidationResult,
    ValidationStatus,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def _no_collision(svc: ActivityTypeService) -> ActivityTypeService:
    """Point the registration collision read at "no platform type under this key".

    ``register`` asks whether the project's usable set already holds this key as
    an opted-in platform type ([R30.02]); the answer is advisory and never blocks,
    but the read still has to be awaitable against a mocked repository. Tests that
    care about the warning stub these two themselves.
    """
    svc._repo.list_platform_by_keys = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return svc


def _no_policy(svc: ActivityTypeService) -> ActivityTypeService:
    """Point the service's policy reader at "no platform policy row".

    That is the permissive fallback, and the behavior every test here was written
    against. Policy enforcement has its own file
    (``test_activity_policy_enforcement.py``); leaving this unstubbed would only
    mean the mocked session cannot serve the policy read.
    """
    svc._policy._repo = MagicMock()
    svc._policy._repo.get_platform = AsyncMock(return_value=None)
    return svc


def _make_type(**over: Any) -> ActivityType:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "key": "quiz",
        "name": "Quiz",
        "payload_schema": _SCHEMA,
        "validator_kind": ValidatorKind.IN_PROCESS,
        "validator_config": {"validator_id": "vid"},
        "retention_days": None,
        "version": 1,
        "created_at": _NOW,
        "deleted_at": None,
    }
    base.update(over)
    return ActivityType(**base)


class TestSchema:
    def test_wellformed_schema_passes(self) -> None:
        validate_schema_wellformed(_SCHEMA)  # no raise

    def test_malformed_schema_rejected(self) -> None:
        with pytest.raises(PayloadSchemaInvalid):
            validate_schema_wellformed({"type": "not-a-type"})

    def test_payload_errors_flags_violation(self) -> None:
        assert payload_errors(_SCHEMA, {})  # missing required 'answer'
        assert payload_errors(_SCHEMA, {"answer": "x"}) == []


class TestRegistry:
    def teardown_method(self) -> None:
        registry.clear_registry()

    async def test_sync_and_async_scorers_run(self) -> None:
        def sync_scorer(payload: dict[str, Any], at: ActivityType, *, db: Any) -> ValidationResult:
            return ValidationResult(is_valid=True, sub_scores={"n": 1})

        async def async_scorer(payload: dict[str, Any], at: ActivityType, *, db: Any) -> ValidationResult:
            return ValidationResult(is_valid=False, error_class="bad")

        registry.register_in_process_validator("s", sync_scorer)
        registry.register_in_process_validator("a", async_scorer)
        assert registry.is_registered("s")

        r1 = await registry.run_in_process_scorer("s", {}, _make_type(), db=MagicMock())
        r2 = await registry.run_in_process_scorer("a", {}, _make_type(), db=MagicMock())
        assert r1.is_valid is True
        assert r2.is_valid is False
        assert r2.error_class == "bad"

    def test_list_registered_returns_id_and_title_sorted(self) -> None:
        registry.register_in_process_validator("zeta", lambda p, a, *, db: ValidationResult(is_valid=True))
        registry.register_in_process_validator(
            "alpha", lambda p, a, *, db: ValidationResult(is_valid=True), title="Alpha scorer"
        )
        listed = registry.list_registered()
        assert [(v.validator_id, v.title) for v in listed] == [
            ("alpha", "Alpha scorer"),
            ("zeta", "zeta"),  # title defaults to the id
        ]

    def test_config_validator_accessor(self) -> None:
        def cfg(config: dict[str, Any]) -> None:
            raise ValidatorConfigInvalid("bad")

        registry.register_in_process_validator(
            "x", lambda p, a, *, db: ValidationResult(is_valid=True), config_validator=cfg
        )
        assert registry.get_config_validator("x") is cfg
        assert registry.get_config_validator("missing") is None


class TestTypeServiceValidatorConfig:
    def teardown_method(self) -> None:
        registry.clear_registry()

    async def test_unknown_in_process_validator_id_rejected(self) -> None:
        svc = _no_policy(ActivityTypeService(MagicMock()))
        with pytest.raises(ValidatorConfigInvalid):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "nope"},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    async def test_webhook_requires_url(self) -> None:
        svc = _no_policy(ActivityTypeService(MagicMock()))
        with pytest.raises(ValidatorConfigInvalid):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.WEBHOOK,
                validator_config={},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    async def test_mcp_non_uuid_agent_id_rejected(self) -> None:
        svc = _no_policy(ActivityTypeService(MagicMock()))
        with pytest.raises(ValidatorConfigInvalid):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.MCP,
                validator_config={
                    "agent_id": "not-a-uuid",
                    "binding_id": str(uuid.uuid4()),
                    "tool_name": "score",
                },
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

    async def test_mcp_valid_uuids_pass_config_validation(self) -> None:
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        type_id = uuid.uuid4()
        svc._repo.create = AsyncMock(return_value=type_id)
        _no_collision(svc)
        svc._repo.get = AsyncMock(return_value=_make_type(id=type_id, validator_kind=ValidatorKind.MCP))
        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.MCP,
                validator_config={
                    "agent_id": str(uuid.uuid4()),
                    "binding_id": str(uuid.uuid4()),
                    "tool_name": "score",
                },
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_awaited_once()

    async def test_malformed_schema_rejected_before_persist(self) -> None:
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        svc._repo.create = AsyncMock()
        with pytest.raises(PayloadSchemaInvalid):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema={"type": "nonsense"},
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "vid"},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_not_awaited()

    async def test_in_process_valid_exact_match_config_passes(self) -> None:
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        type_id = uuid.uuid4()
        svc._repo.create = AsyncMock(return_value=type_id)
        _no_collision(svc)
        svc._repo.get = AsyncMock(return_value=_make_type(id=type_id))
        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "exact_match", "field": "answer", "expected": "42"},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_awaited_once()

    async def test_in_process_exact_match_missing_field_rejected(self) -> None:
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        svc._repo.create = AsyncMock()
        with pytest.raises(ValidatorConfigInvalid):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "exact_match", "expected": "42"},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_not_awaited()

    async def test_in_process_valid_filled_count_config_passes(self) -> None:
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        type_id = uuid.uuid4()
        svc._repo.create = AsyncMock(return_value=type_id)
        _no_collision(svc)
        svc._repo.get = AsyncMock(return_value=_make_type(id=type_id))
        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 0},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_awaited_once()

    @pytest.mark.parametrize(
        "bad_config",
        [
            {"validator_id": "filled_count"},
            {"validator_id": "filled_count", "min_filled": -1},
            {"validator_id": "filled_count", "min_filled": True},
            {"validator_id": "filled_count", "min_filled": "3"},
        ],
        ids=["missing", "negative", "bool", "string"],
    )
    async def test_in_process_filled_count_bad_config_rejected(self, bad_config: dict[str, Any]) -> None:
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        svc._repo.create = AsyncMock()
        with pytest.raises(ValidatorConfigInvalid):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config=bad_config,
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_not_awaited()

    async def test_a_min_filled_above_the_property_count_is_rejected_at_register(self) -> None:
        """F-7. `_SCHEMA` declares one property, so no submission could ever reach
        99 and the activity would be permanently unpassable. The rule lives on the
        registry's `schema_config_validator` hook, which the course loader has
        always called and this write path never did."""
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        svc._repo.create = AsyncMock()
        with pytest.raises(ValidatorConfigInvalid, match="min_filled"):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 99},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_not_awaited()

    async def test_a_min_filled_equal_to_the_property_count_is_accepted(self) -> None:
        """AC-7. The check uses `>`: a threshold every field must be filled to reach
        is a legal, and shipped, configuration."""
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        type_id = uuid.uuid4()
        svc._repo.create = AsyncMock(return_value=type_id)
        _no_collision(svc)
        svc._repo.get = AsyncMock(return_value=_make_type(id=type_id))
        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            await svc.register(
                project_id=uuid.uuid4(),
                key="k",
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": len(_SCHEMA["properties"])},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.create.assert_awaited_once()

    async def test_a_min_filled_above_the_property_count_is_rejected_at_edit(self) -> None:
        """F-7's other write path: `update` shares the same single validator gate."""
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        project_id = uuid.uuid4()
        type_id = uuid.uuid4()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        svc._repo.update = AsyncMock()
        svc._repo.get = AsyncMock(
            return_value=_make_type(
                id=type_id,
                project_id=project_id,
                validator_config={"validator_id": "filled_count", "min_filled": 1},
            )
        )
        svc._activation_repo = MagicMock()
        svc._activation_repo.list_active_for_type = AsyncMock(return_value=[])
        with pytest.raises(ValidatorConfigInvalid, match="min_filled"):
            await svc.update(
                project_id=project_id,
                type_id=type_id,
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 99},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.update.assert_not_awaited()

    async def test_a_metadata_only_edit_of_a_stored_violating_type_still_succeeds(self) -> None:
        """AC-8. This is why F-7 needs no data migration: the validator gate runs
        only inside `if behavioral_changed`, so a row stored before the fix (or by
        a direct DB write) still accepts a rename or a retention change. A future
        refactor of `update` that hoists the gate out of that branch would strand
        every such row, which is what this pins."""
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        project_id = uuid.uuid4()
        type_id = uuid.uuid4()
        violating = {"validator_id": "filled_count", "min_filled": 99}
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        svc._repo.update = AsyncMock(return_value=True)
        svc._repo.get = AsyncMock(
            return_value=_make_type(id=type_id, project_id=project_id, validator_config=violating)
        )
        svc._activation_repo = MagicMock()
        svc._activation_repo.list_active_for_type = AsyncMock(return_value=[])

        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            await svc.update(
                project_id=project_id,
                type_id=type_id,
                name="A new name",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config=violating,
                retention_days=30,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )

        svc._repo.update.assert_awaited_once()
        assert svc._repo.update.await_args.kwargs["bump_version"] is False

    async def test_edit_to_bad_filled_count_config_rejected(self) -> None:
        """The same config gate runs on the edit path, not only registration (R30.23)."""
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        project_id = uuid.uuid4()
        type_id = uuid.uuid4()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        svc._repo = MagicMock()
        svc._repo.update = AsyncMock()
        svc._repo.get = AsyncMock(
            return_value=_make_type(
                id=type_id,
                project_id=project_id,
                validator_config={"validator_id": "filled_count", "min_filled": 2},
            )
        )
        svc._activation_repo = MagicMock()
        svc._activation_repo.list_active_for_type = AsyncMock(return_value=[])
        with pytest.raises(ValidatorConfigInvalid):
            await svc.update(
                project_id=project_id,
                type_id=type_id,
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": -3},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc._repo.update.assert_not_awaited()


class TestCrossScopeKeyCollisionWarning:
    """[R30.02]: a project may author a type whose key names a platform type it
    opted into. Permitted -- a project-scoped copy of a shipped example is a
    supported outcome, and the CLI seeder's approved behaviour depends on it --
    but the acting owner is warned, because everything that resolves a type by key
    alone then resolves two.
    """

    def teardown_method(self) -> None:
        registry.clear_registry()

    def _svc(
        self, *, platform_rows: list[ActivityType], opted_in: bool
    ) -> tuple[ActivityTypeService, uuid.UUID]:
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        svc = _no_policy(ActivityTypeService(MagicMock()))
        type_id = uuid.uuid4()
        svc._repo = MagicMock()
        svc._repo.create = AsyncMock(return_value=type_id)
        svc._repo.get = AsyncMock(return_value=_make_type(id=type_id, key="mandala-9grid"))
        svc._repo.list_platform_by_keys = AsyncMock(return_value=platform_rows)
        svc._optin_repo = MagicMock()
        svc._optin_repo.exists = AsyncMock(return_value=opted_in)
        return svc, type_id

    async def _register(
        self, svc: ActivityTypeService, *, project_id: uuid.UUID, key: str = "mandala-9grid", **over: Any
    ) -> Any:
        with patch("contexts.activities.application.type_service.audit.emit", new=AsyncMock()):
            return await svc.register(
                project_id=project_id,
                key=key,
                name="n",
                payload_schema=_SCHEMA,
                validator_kind=ValidatorKind.IN_PROCESS,
                validator_config={"validator_id": "filled_count", "min_filled": 0},
                retention_days=None,
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
                **over,
            )

    async def test_it_warns_and_still_creates_the_type(self) -> None:
        """AC-1. The warning is advisory: the row must exist afterwards."""
        platform_row = _make_type(project_id=None, key="mandala-9grid", scope=ActivityTypeScope.PLATFORM)
        svc, _ = self._svc(platform_rows=[platform_row], opted_in=True)
        project_id = uuid.uuid4()

        result = await self._register(svc, project_id=project_id)

        assert result.shadowed_by_platform is True
        assert result.activity_type.key == "mandala-9grid"
        svc._repo.create.assert_awaited_once()
        svc._optin_repo.exists.assert_awaited_once_with(
            project_id=project_id, activity_type_id=platform_row.id
        )

    async def test_no_warning_when_the_project_has_not_opted_in(self) -> None:
        """AC-3's negative half. A platform type nobody enabled is not in this
        project's usable set, so there is no collision to report."""
        platform_row = _make_type(project_id=None, key="mandala-9grid", scope=ActivityTypeScope.PLATFORM)
        svc, _ = self._svc(platform_rows=[platform_row], opted_in=False)

        result = await self._register(svc, project_id=uuid.uuid4())

        assert result.shadowed_by_platform is False
        svc._repo.create.assert_awaited_once()

    async def test_no_warning_when_no_platform_type_carries_the_key(self) -> None:
        svc, _ = self._svc(platform_rows=[], opted_in=True)

        result = await self._register(svc, project_id=uuid.uuid4(), key="something-else")

        assert result.shadowed_by_platform is False
        svc._optin_repo.exists.assert_not_awaited()

    async def test_the_platform_install_path_is_unaffected(self) -> None:
        """AC-4. `install_course` registers with `project_id=None`, which has no
        usable set to shadow -- and the opt-in read would have no project to ask
        about. Scope-conditioning the check is what keeps that path untouched."""
        svc, type_id = self._svc(platform_rows=[], opted_in=True)
        svc._repo.get = AsyncMock(
            return_value=_make_type(
                id=type_id, project_id=None, key="mandala-9grid", scope=ActivityTypeScope.PLATFORM
            )
        )

        result = await self._register(svc, project_id=None, scope=ActivityTypeScope.PLATFORM)

        assert result.shadowed_by_platform is False
        svc._repo.list_platform_by_keys.assert_not_awaited()
        svc._optin_repo.exists.assert_not_awaited()

    async def test_no_new_error_slug_is_raised(self) -> None:
        """AC-3. A refusal here would be wrong twice over: it would overturn the
        approved CLI-seeder decision, and `ActivityTypeForm` maps any non-policy
        409 to "this key already exists" -- so a new 409 slug would inherit a
        message describing a different situation."""
        platform_row = _make_type(project_id=None, key="mandala-9grid", scope=ActivityTypeScope.PLATFORM)
        svc, _ = self._svc(platform_rows=[platform_row], opted_in=True)

        result = await self._register(svc, project_id=uuid.uuid4())  # no raise

        assert result.activity_type is not None


class TestExactMatchValidator:
    """The first-party ``exact_match`` scorer + its config validator (AC-2, AC-3)."""

    def teardown_method(self) -> None:
        registry.clear_registry()

    def _score(self, config: dict[str, Any], payload: dict[str, Any]) -> ValidationResult:
        from app.plugins.activity_validators import exact_match_scorer

        at = _make_type(validator_config={"validator_id": "exact_match", **config})
        return exact_match_scorer(payload, at, db=MagicMock())

    def test_exact_match_valid(self) -> None:
        r = self._score({"field": "answer", "expected": "42"}, {"answer": "42"})
        assert r.is_valid is True
        assert r.error_class is None

    def test_exact_match_mismatch(self) -> None:
        r = self._score({"field": "answer", "expected": "42"}, {"answer": "43"})
        assert r.is_valid is False
        assert r.error_class == "mismatch"

    def test_exact_match_case_insensitive_by_default(self) -> None:
        assert self._score({"field": "answer", "expected": "Yes"}, {"answer": "yes"}).is_valid is True

    def test_exact_match_case_sensitive_when_set(self) -> None:
        r = self._score({"field": "answer", "expected": "Yes", "case_sensitive": True}, {"answer": "yes"})
        assert r.is_valid is False

    def test_exact_match_non_string_equality(self) -> None:
        assert self._score({"field": "n", "expected": 7}, {"n": 7}).is_valid is True
        assert self._score({"field": "ok", "expected": True}, {"ok": False}).is_valid is False

    def test_exact_match_missing_payload_field_is_mismatch(self) -> None:
        r = self._score({"field": "answer", "expected": "42"}, {})
        assert r.is_valid is False
        assert r.error_class == "mismatch"

    def test_config_validator_accepts_wellformed(self) -> None:
        from app.plugins.activity_validators import validate_exact_match_config

        validate_exact_match_config({"validator_id": "exact_match", "field": "answer", "expected": "42"})

    def test_config_validator_rejects_empty_field(self) -> None:
        from app.plugins.activity_validators import validate_exact_match_config

        with pytest.raises(ValidatorConfigInvalid):
            validate_exact_match_config({"field": "  ", "expected": "42"})

    def test_config_validator_rejects_missing_expected(self) -> None:
        from app.plugins.activity_validators import validate_exact_match_config

        with pytest.raises(ValidatorConfigInvalid):
            validate_exact_match_config({"field": "answer"})


class TestFilledCountValidator:
    """The first-party ``filled_count`` scorer + its config validator (AC-2..AC-5)."""

    def teardown_method(self) -> None:
        registry.clear_registry()

    def _score(
        self,
        config: dict[str, Any],
        payload: dict[str, Any],
        *,
        declared: list[str] | None = None,
    ) -> ValidationResult:
        """Score ``payload`` against a type declaring ``declared`` (default: the
        payload's own keys, i.e. a submission that stayed inside the schema)."""
        from app.plugins.activity_validators import filled_count_scorer

        names = declared if declared is not None else list(payload)
        at = _make_type(
            payload_schema={"type": "object", "properties": {n: {"type": "string"} for n in names}},
            validator_config={"validator_id": "filled_count", **config},
        )
        return filled_count_scorer(payload, at, db=MagicMock())

    def test_meets_threshold_is_valid(self) -> None:
        r = self._score({"min_filled": 2}, {"a": "x", "b": "y", "c": ""})
        assert r.is_valid is True
        assert r.error_class is None
        assert r.sub_scores == {"filled": 2}

    def test_below_threshold_is_invalid(self) -> None:
        r = self._score({"min_filled": 3}, {"a": "x", "b": "", "c": None})
        assert r.is_valid is False
        assert r.error_class == "too_few_filled"
        assert r.sub_scores == {"filled": 1}

    def test_whitespace_only_string_is_not_filled(self) -> None:
        assert self._score({"min_filled": 0}, {"a": "   ", "b": "\n\t"}).sub_scores == {"filled": 0}

    def test_empty_collections_are_not_filled(self) -> None:
        r = self._score({"min_filled": 0}, {"a": [], "b": {}, "c": ["x"], "d": {"k": 1}})
        assert r.sub_scores == {"filled": 2}

    def test_zero_counts_but_false_does_not(self) -> None:
        # A numeric field is only present when something was typed, so 0 is an
        # answer. A boolean is submitted whether or not it was touched, so False
        # is indistinguishable from untouched and must not count.
        assert self._score({"min_filled": 0}, {"a": 0, "b": False}).sub_scores == {"filled": 1}

    def test_an_untouched_checkbox_schema_scores_zero(self) -> None:
        """Counting False would make the metric report the schema's size.

        Three declared booleans, nothing filled in: the generic form still submits
        `false` for each, so counting them would score filled=3 and clear any
        threshold up to 3 on an empty submission.
        """
        r = self._score({"min_filled": 3}, {"a": False, "b": False, "c": False})
        assert r.sub_scores == {"filled": 0}
        assert r.is_valid is False
        assert r.error_class == "too_few_filled"

    def test_a_ticked_checkbox_counts(self) -> None:
        assert self._score({"min_filled": 1}, {"a": True, "b": False}).sub_scores == {"filled": 1}

    def test_min_filled_zero_is_collect_only(self) -> None:
        r = self._score({"min_filled": 0}, {"a": "", "b": None})
        assert r.is_valid is True
        assert r.sub_scores == {"filled": 0}

    def test_absent_min_filled_defaults_to_collect_only(self) -> None:
        assert self._score({}, {"a": ""}).is_valid is True

    def test_sub_scores_never_leak_validator_config(self) -> None:
        """``sub_scores`` is participant-visible; ``validator_config`` is owner-only (R30.25)."""
        r = self._score({"min_filled": 5}, {"a": "x"})
        assert set(r.sub_scores) == {"filled"}

    def test_undeclared_payload_keys_are_not_counted(self) -> None:
        """A participant must not clear the threshold by padding the submission.

        JSON Schema permits additional properties unless a schema forbids them, so
        the payload can legally carry keys the type never declared. Counting those
        would let a room member pass ``min_filled`` — and inflate the reported
        fluency count — with one real answer plus filler.
        """
        r = self._score(
            {"min_filled": 4},
            {"center": "x", "zz1": "a", "zz2": "a", "zz3": "a", "zz4": "a"},
            declared=["center", "cell_1", "cell_2", "cell_3"],
        )
        assert r.is_valid is False
        assert r.error_class == "too_few_filled"
        assert r.sub_scores == {"filled": 1}

    def test_declared_but_absent_fields_count_as_unfilled(self) -> None:
        r = self._score({"min_filled": 0}, {"a": "x"}, declared=["a", "b", "c"])
        assert r.sub_scores == {"filled": 1}

    def test_config_validator_accepts_zero(self) -> None:
        from app.plugins.activity_validators import validate_filled_count_config

        validate_filled_count_config({"validator_id": "filled_count", "min_filled": 0})

    @pytest.mark.parametrize(
        "bad",
        [{}, {"min_filled": -1}, {"min_filled": True}, {"min_filled": "3"}, {"min_filled": 1.5}],
        ids=["missing", "negative", "bool", "string", "float"],
    )
    def test_config_validator_rejects(self, bad: dict[str, Any]) -> None:
        from app.plugins.activity_validators import validate_filled_count_config

        with pytest.raises(ValidatorConfigInvalid):
            validate_filled_count_config(bad)


class TestIsFilledRule:
    """The whole of ``_is_filled``, in one executable statement of the rule.

    A **characterization** test, not a regression test: nothing here fails before
    the change that added it, because the code was already right. Its job is that
    the rule has one authoritative place to be read off.
    ``docs/examples/creative-thinking-course.md`` paraphrases this function for
    educators authoring their own schemas, and once stated the boolean case
    exactly backwards; a prose paraphrase and a scorer test that reaches the rule
    through ``filled_count_scorer`` both left the rule itself unpinned.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, False),
            (False, False),
            (True, True),
            ("", False),
            ("   \n\t ", False),
            ("x", True),
            (0, True),
            (1, True),
            (0.0, True),
            ([], False),
            ({}, False),
            ([1], True),
            ({"k": 1}, True),
        ],
        ids=[
            "none",
            "false",
            "true",
            "empty-string",
            "whitespace-string",
            "non-empty-string",
            "zero",
            "one",
            "zero-float",
            "empty-list",
            "empty-dict",
            "non-empty-list",
            "non-empty-dict",
        ],
    )
    def test_is_filled(self, value: Any, expected: bool) -> None:
        from app.plugins.activity_validators import _is_filled

        assert _is_filled(value) is expected


class TestFilledCountSchemaConfigValidator:
    """The cross-field rule the config-only validator structurally cannot state.

    ``min_filled`` above the declared property count is an activity nobody can
    pass. The scorer counts declared properties, so the threshold has to be
    checked against the schema — which a ``ConfigValidator`` never sees. It lives
    on the same side of the boundary as the validator it belongs to, reached
    through the registry's ``schema_config_validator`` hook.
    """

    @staticmethod
    def _schema(*names: str) -> dict[str, Any]:
        return {"type": "object", "properties": {n: {"type": "string"} for n in names}}

    def test_a_threshold_at_the_property_count_is_allowed(self) -> None:
        from app.plugins.activity_validators import validate_filled_count_against_schema

        validate_filled_count_against_schema({"min_filled": 2}, self._schema("a", "b"))

    def test_a_threshold_above_the_property_count_is_rejected(self) -> None:
        from app.plugins.activity_validators import validate_filled_count_against_schema

        with pytest.raises(ValidatorConfigInvalid, match="min_filled"):
            validate_filled_count_against_schema({"min_filled": 3}, self._schema("a", "b"))

    def test_it_defers_the_type_error_to_the_config_validator(self) -> None:
        """A non-integer threshold is ``validate_filled_count_config``'s to report;
        raising here too would surface whichever ran first, which is not a stable
        message for a client to render."""
        from app.plugins.activity_validators import validate_filled_count_against_schema

        validate_filled_count_against_schema({"min_filled": "3"}, self._schema("a"))

    def test_a_schema_without_properties_is_left_to_the_wellformedness_check(self) -> None:
        from app.plugins.activity_validators import validate_filled_count_against_schema

        validate_filled_count_against_schema({"min_filled": 5}, {"type": "object"})

    def test_the_hook_is_reachable_through_the_registry(self) -> None:
        """The wiring, not just the function: an unregistered hook would leave the
        catalogue silently accepting an unreachable threshold."""
        from app.plugins.activity_validators import FILLED_COUNT_ID, register_first_party_validators

        register_first_party_validators()
        hook = registry.get_schema_config_validator(FILLED_COUNT_ID)

        assert hook is not None
        with pytest.raises(ValidatorConfigInvalid):
            hook({"min_filled": 9}, self._schema("a"))


class TestActivityValidatorRegistrationWiring:
    """The bootstrap step registers the shipped set (AC-1)."""

    def teardown_method(self) -> None:
        registry.clear_registry()

    async def test_startup_step_registers_exact_match(self) -> None:
        from app.bootstrap.startup import register_activity_validators_step

        registry.clear_registry()
        assert not registry.is_registered("exact_match")
        await register_activity_validators_step(MagicMock())
        assert registry.is_registered("exact_match")
        assert any(v.validator_id == "exact_match" for v in registry.list_registered())

    async def test_startup_step_registers_filled_count(self) -> None:
        from app.bootstrap.startup import register_activity_validators_step

        registry.clear_registry()
        assert not registry.is_registered("filled_count")
        await register_activity_validators_step(MagicMock())
        assert registry.is_registered("filled_count")
        listed = {v.validator_id: v.title for v in registry.list_registered()}
        assert listed["filled_count"] == "Filled count"


def _wire_submission_service(
    activity_type: ActivityType,
) -> tuple[SubmissionService, MagicMock, ActivitySession]:
    session = ActivitySession(
        id=uuid.uuid4(),
        activity_type_id=activity_type.id,
        chatroom_id=uuid.uuid4(),
        subject_user_id=uuid.uuid4(),
        status=SessionStatus.OPEN,
        created_at=_NOW,
    )
    activation_repo = MagicMock()
    svc = SubmissionService(MagicMock(), activation_repo=activation_repo)
    svc._type_repo = MagicMock()
    svc._type_repo.get = AsyncMock(return_value=activity_type)
    svc._session_repo = MagicMock()
    svc._session_repo.get_open = AsyncMock(return_value=session)
    svc._session_repo.lock_for_update = AsyncMock(return_value=session)
    activation_repo.get_active_for_update = AsyncMock(
        return_value=ActivityActivation(
            id=uuid.uuid4(),
            chatroom_id=session.chatroom_id,
            activity_type_id=activity_type.id,
            started_by_user_id=session.subject_user_id,
            status=ActivationStatus.ACTIVE,
            created_at=_NOW,
        )
    )
    sub_id = uuid.uuid4()
    svc._sub_repo = MagicMock()
    svc._sub_repo.next_attempt_no = AsyncMock(return_value=1)
    svc._sub_repo.insert = AsyncMock(return_value=sub_id)
    svc._sub_repo.count_recent_same_error = AsyncMock(return_value=0)
    svc._sub_repo.get = AsyncMock(
        return_value=ActivitySubmission(
            id=sub_id,
            session_id=session.id,
            activity_type_id=activity_type.id,
            chatroom_id=session.chatroom_id,
            producer_user_id=uuid.uuid4(),
            payload={},
            attempt_no=1,
            validation_status=ValidationStatus.VALIDATED,
            is_valid=True,
            error_class=None,
            sub_scores={},
            latency_ms=1,
            retain_until=None,
            created_at=_NOW,
            validated_at=_NOW,
        )
    )
    return svc, svc._sub_repo, session


class TestSubmitInProcess:
    def teardown_method(self) -> None:
        registry.clear_registry()

    async def test_in_process_scores_server_side_and_ignores_client_score(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4())

        def scorer(payload: dict[str, Any], at: ActivityType, *, db: Any) -> ValidationResult:
            return ValidationResult(is_valid=True, sub_scores={"grade": 100})

        registry.register_in_process_validator("vid", scorer)
        svc, sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                # Client tries to forge a passing score / attempt number:
                payload={"answer": "x", "is_valid": False, "attempt_no": 99, "score": 0},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )

        kwargs = sub_repo.insert.await_args.kwargs
        assert kwargs["validation_status"] is ValidationStatus.VALIDATED
        assert kwargs["is_valid"] is True  # from the server scorer, not the client
        assert kwargs["sub_scores"] == {"grade": 100}
        assert kwargs["attempt_no"] == 1  # server-assigned (max 0 + 1), not client's 99

    async def test_exact_match_scores_end_to_end(self) -> None:
        from app.plugins.activity_validators import register_first_party_validators

        register_first_party_validators()
        activity_type = _make_type(
            project_id=uuid.uuid4(),
            validator_config={"validator_id": "exact_match", "field": "answer", "expected": "42"},
        )
        svc, sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "43"},  # wrong answer
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )

        kwargs = sub_repo.insert.await_args.kwargs
        assert kwargs["validation_status"] is ValidationStatus.VALIDATED
        assert kwargs["is_valid"] is False
        assert kwargs["error_class"] == "mismatch"

    async def test_payload_schema_violation_rejected(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4())
        registry.register_in_process_validator("vid", lambda p, a, *, db: ValidationResult(is_valid=True))
        svc, sub_repo, session = _wire_submission_service(activity_type)

        with pytest.raises(SubmissionPayloadInvalid):
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={},  # missing required 'answer'
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )
        sub_repo.insert.assert_not_awaited()

    async def test_inactive_or_wrong_type_is_rejected_before_session_resolution(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4())
        svc, sub_repo, session = _wire_submission_service(activity_type)
        svc._activation_repo.get_active_for_update = AsyncMock(return_value=None)

        with pytest.raises(ActivityNotActive):
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "x"},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )

        svc._session_repo.get_open.assert_not_awaited()
        sub_repo.insert.assert_not_awaited()

    async def test_in_process_scorer_exception_recorded_as_error(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4())

        def boom(payload: dict[str, Any], at: ActivityType, *, db: Any) -> ValidationResult:
            raise RuntimeError("scorer bug")

        registry.register_in_process_validator("vid", boom)
        svc, sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            # A scorer bug must NOT surface as a 500 / lost submission.
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "x"},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )

        kwargs = sub_repo.insert.await_args.kwargs
        assert kwargs["validation_status"] is ValidationStatus.ERROR
        assert kwargs["is_valid"] is None
        assert kwargs["error_class"] == "validator_error"


class TestAgentDigest:
    """Agent-visibility follow-up: submit-time digest computation + echo gating."""

    def teardown_method(self) -> None:
        registry.clear_registry()

    async def test_submit_stores_payload_fallback_digest_when_no_detail(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4())
        registry.register_in_process_validator("vid", lambda p, a, *, db: ValidationResult(is_valid=True))
        svc, sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "x"},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )

        kwargs = sub_repo.insert.await_args.kwargs
        assert kwargs["agent_digest"] == '{"answer":"x"}'

    async def test_submit_prefers_validator_detail_over_payload(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4())
        registry.register_in_process_validator(
            "vid", lambda p, a, *, db: ValidationResult(is_valid=True, detail="drew a red circle")
        )
        svc, sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "x"},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )

        kwargs = sub_repo.insert.await_args.kwargs
        assert kwargs["agent_digest"] == "drew a red circle"

    async def test_echo_omits_content_by_default(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4())  # echo_includes_content defaults False
        registry.register_in_process_validator("vid", lambda p, a, *, db: ValidationResult(is_valid=True))
        svc, _sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "x"},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )
            echo_kwargs = conv.return_value.insert_system_message.await_args.kwargs

        assert "Content:" not in echo_kwargs["content_md"]

    async def test_echo_includes_content_when_type_opts_in(self) -> None:
        activity_type = _make_type(project_id=uuid.uuid4(), echo_includes_content=True)
        registry.register_in_process_validator("vid", lambda p, a, *, db: ValidationResult(is_valid=True))
        svc, _sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "x"},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )
            echo_kwargs = conv.return_value.insert_system_message.await_args.kwargs

        assert 'Content: {"answer":"x"}' in echo_kwargs["content_md"]

    async def test_echo_omits_content_when_agent_visibility_is_off_even_if_echo_opts_in(self) -> None:
        """Adversarial-review fix: echo_includes_content cannot show content the
        type owner turned off for agents — a chat message visible to humans is
        visible to every agent reading the same room transcript, so
        expose_payload_to_agent=False must win over echo_includes_content=True."""
        activity_type = _make_type(
            project_id=uuid.uuid4(), expose_payload_to_agent=False, echo_includes_content=True
        )
        registry.register_in_process_validator("vid", lambda p, a, *, db: ValidationResult(is_valid=True))
        svc, _sub_repo, session = _wire_submission_service(activity_type)

        with (
            patch.object(ss, "ConversationFacade") as conv,
            patch.object(ss.audit, "emit", new=AsyncMock()),
        ):
            conv.return_value.insert_system_message = AsyncMock()
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=session.subject_user_id,
                subject_user_id=session.subject_user_id,
                caller_user_id=session.subject_user_id,
                payload={"answer": "x"},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )
            echo_kwargs = conv.return_value.insert_system_message.await_args.kwargs

        assert "Content:" not in echo_kwargs["content_md"]


def _make_submission(**over: Any) -> ActivitySubmission:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "activity_type_id": uuid.uuid4(),
        "chatroom_id": uuid.uuid4(),
        "producer_user_id": uuid.uuid4(),
        "payload": {},
        "attempt_no": 2,
        "validation_status": ValidationStatus.VALIDATED,
        "is_valid": False,
        "error_class": "wrong_component",
        "sub_scores": {},
        "latency_ms": 42,
        "retain_until": None,
        "created_at": _NOW,
        "validated_at": _NOW,
    }
    base.update(over)
    return ActivitySubmission(**base)


def _wire_signal_service(
    submission: ActivitySubmission, activity_type: ActivityType, *, same_error_count: int = 0
) -> tuple[SubmissionService, MagicMock]:
    session = ActivitySession(
        id=submission.session_id,
        activity_type_id=submission.activity_type_id,
        chatroom_id=submission.chatroom_id,
        subject_user_id=uuid.uuid4(),
        status=SessionStatus.OPEN,
        created_at=_NOW,
    )
    svc = SubmissionService(MagicMock(), activation_repo=MagicMock())
    svc._sub_repo = MagicMock()
    svc._sub_repo.get = AsyncMock(return_value=submission)
    svc._sub_repo.count_recent_same_error = AsyncMock(return_value=same_error_count)
    svc._type_repo = MagicMock()
    svc._type_repo.get = AsyncMock(return_value=activity_type)
    svc._session_repo = MagicMock()
    svc._session_repo.get = AsyncMock(return_value=session)
    return svc, svc._sub_repo


class TestBuildActivitySignal:
    """AC-1: the reactive-rules signal payload — numeric rolling on completion,
    no error_class/rolling while pending, all fields from the authoritative row."""

    async def test_completion_attaches_numeric_rolling(self) -> None:
        activity_type = _make_type(key="quiz")
        submission = _make_submission(
            activity_type_id=activity_type.id, error_class="wrong_component", latency_ms=42
        )
        svc, sub_repo = _wire_signal_service(
            activity_type=activity_type, submission=submission, same_error_count=3
        )

        payload = await svc.build_activity_signal(submission_id=submission.id)

        assert payload is not None
        assert payload["activity_type_key"] == "quiz"
        assert payload["validation_status"] == "validated"
        assert payload["error_class"] == "wrong_component"
        rolling = payload["rolling"]
        assert rolling["same_error_count"] == 3
        assert isinstance(rolling["same_error_count"], int)
        assert rolling["window_seconds"] == ss._ROLLING_WINDOW_SECONDS
        assert rolling["latency_ms"] == 42
        sub_repo.count_recent_same_error.assert_awaited_once()

    async def test_completion_without_error_class_counts_zero(self) -> None:
        activity_type = _make_type()
        submission = _make_submission(activity_type_id=activity_type.id, is_valid=True, error_class=None)
        svc, sub_repo = _wire_signal_service(activity_type=activity_type, submission=submission)

        payload = await svc.build_activity_signal(submission_id=submission.id)

        assert payload is not None
        assert payload["rolling"]["same_error_count"] == 0
        # No error class → the count query is skipped entirely.
        sub_repo.count_recent_same_error.assert_not_awaited()

    async def test_pending_carries_zeroed_numeric_rolling(self) -> None:
        activity_type = _make_type()
        submission = _make_submission(
            activity_type_id=activity_type.id,
            validation_status=ValidationStatus.PENDING,
            is_valid=None,
            error_class=None,
            latency_ms=None,
            validated_at=None,
        )
        svc, sub_repo = _wire_signal_service(activity_type=activity_type, submission=submission)

        payload = await svc.build_activity_signal(submission_id=submission.id)

        assert payload is not None
        assert payload["submission_id"] == str(submission.id)
        assert payload["validation_status"] == "pending"
        assert payload["error_class"] is None
        # rolling is ALWAYS present and numeric so int({{trigger.rolling.*}}) never
        # dereferences None even on the pending submit emit.
        rolling = payload["rolling"]
        assert rolling["same_error_count"] == 0
        assert rolling["latency_ms"] == 0
        assert isinstance(rolling["latency_ms"], int)
        # No error class yet → the count query is skipped.
        sub_repo.count_recent_same_error.assert_not_awaited()

    async def test_missing_submission_returns_none(self) -> None:
        svc = SubmissionService(MagicMock(), activation_repo=MagicMock())
        svc._sub_repo = MagicMock()
        svc._sub_repo.get = AsyncMock(return_value=None)

        assert await svc.build_activity_signal(submission_id=uuid.uuid4()) is None

    async def test_it_carries_the_type_id_and_scope_so_a_rule_can_disambiguate(self) -> None:
        """AC-6. `activity_type_key` alone no longer names one type: a project's
        usable set may hold its own type and an opted-in platform type under the
        same key ([R30.02]). Both fields follow the payload's always-present
        discipline, so an SEL rule can dereference them without a null check."""
        activity_type = _make_type(key="mandala-9grid", scope=ActivityTypeScope.PLATFORM, project_id=None)
        submission = _make_submission(activity_type_id=activity_type.id)
        svc, _ = _wire_signal_service(activity_type=activity_type, submission=submission)

        payload = await svc.build_activity_signal(submission_id=submission.id)

        assert payload is not None
        assert payload["activity_type_key"] == "mandala-9grid"
        assert payload["activity_type_id"] == str(activity_type.id)
        assert payload["activity_type_scope"] == "platform"

    async def test_a_project_scoped_type_reports_the_project_scope(self) -> None:
        activity_type = _make_type(key="mandala-9grid")
        submission = _make_submission(activity_type_id=activity_type.id)
        svc, _ = _wire_signal_service(activity_type=activity_type, submission=submission)

        payload = await svc.build_activity_signal(submission_id=submission.id)

        assert payload is not None
        assert payload["activity_type_scope"] == "project"

    async def test_a_vanished_type_still_yields_present_string_fields(self) -> None:
        """The re-read path can find the submission but not its type (deleted
        between submit and validation). `key` already degraded to `""` there;
        `scope` must degrade the same way rather than becoming None and breaking
        an SEL rule that dereferences it."""
        activity_type = _make_type()
        submission = _make_submission(activity_type_id=activity_type.id)
        svc, _ = _wire_signal_service(activity_type=activity_type, submission=submission)
        svc._type_repo.get = AsyncMock(return_value=None)

        payload = await svc.build_activity_signal(submission_id=submission.id)

        assert payload is not None
        assert payload["activity_type_key"] == ""
        assert payload["activity_type_scope"] == ""
        # The id comes from the submission row, which is still there.
        assert payload["activity_type_id"] == str(activity_type.id)


class TestRecordValidationDigest:
    """Agent-visibility follow-up: async write-back refines the digest only when
    the remote validator supplied ``detail``."""

    async def test_overwrites_digest_when_detail_present(self) -> None:
        activity_type = _make_type()
        submission = _make_submission(activity_type_id=activity_type.id)
        svc, sub_repo = _wire_signal_service(activity_type=activity_type, submission=submission)
        sub_repo.record_validation = AsyncMock(return_value=True)

        with patch.object(ss.audit, "emit", new=AsyncMock()):
            await svc.record_validation(
                submission_id=submission.id,
                result=ValidationResult(is_valid=True, detail="a rich description"),
                latency_ms=5,
            )

        kwargs = sub_repo.record_validation.await_args.kwargs
        assert kwargs["agent_digest"] == "a rich description"

    async def test_leaves_digest_untouched_when_no_detail(self) -> None:
        activity_type = _make_type()
        submission = _make_submission(activity_type_id=activity_type.id)
        svc, sub_repo = _wire_signal_service(activity_type=activity_type, submission=submission)
        sub_repo.record_validation = AsyncMock(return_value=True)

        with patch.object(ss.audit, "emit", new=AsyncMock()):
            await svc.record_validation(
                submission_id=submission.id,
                result=ValidationResult(is_valid=True),
                latency_ms=5,
            )

        kwargs = sub_repo.record_validation.await_args.kwargs
        assert kwargs["agent_digest"] is None


class TestOpenSessionTenantIsolation:
    async def test_cross_project_type_rejected(self) -> None:
        from contexts.activities.application.session_service import ActivitySessionService
        from contexts.activities.domain.errors import ActivityTypeNotFound

        svc = ActivitySessionService(MagicMock())
        svc._type_repo = MagicMock()
        # Type belongs to a different project than the caller's room project.
        svc._type_repo.get = AsyncMock(return_value=_make_type(project_id=uuid.uuid4()))
        svc._repo = MagicMock()
        svc._repo.get_open = AsyncMock()

        with pytest.raises(ActivityTypeNotFound):
            await svc.open_session(
                project_id=uuid.uuid4(),  # not the type's project
                activity_type_id=uuid.uuid4(),
                chatroom_id=uuid.uuid4(),
                subject_user_id=uuid.uuid4(),
                caller_user_id=uuid.uuid4(),
            )
        # Never touched the session table for a foreign type.
        svc._repo.get_open.assert_not_awaited()

    async def test_opening_a_session_for_another_subject_is_refused(self) -> None:
        """T-2: a room member may not open a session naming a foreign subject."""
        activity_type = _make_type()
        svc = ActivitySessionService(MagicMock())
        svc._type_repo = MagicMock()
        svc._type_repo.get = AsyncMock(return_value=activity_type)
        svc._repo = MagicMock()
        svc._repo.get_open = AsyncMock()
        svc._repo.create_open = AsyncMock()

        with pytest.raises(SessionNotFound):
            await svc.open_session(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=uuid.uuid4(),
                subject_user_id=uuid.uuid4(),  # subject B
                caller_user_id=uuid.uuid4(),  # caller A != B
            )
        # Rejected before any session resolution.
        svc._repo.get_open.assert_not_awaited()

    async def test_admin_may_open_a_session_for_any_subject(self) -> None:
        """T-4 (open arm): caller_user_id=None (admin) skips the subject check."""
        activity_type = _make_type()
        session = ActivitySession(
            id=uuid.uuid4(),
            activity_type_id=activity_type.id,
            chatroom_id=uuid.uuid4(),
            subject_user_id=uuid.uuid4(),
            status=SessionStatus.OPEN,
            created_at=_NOW,
        )
        svc = ActivitySessionService(MagicMock())
        svc._type_repo = MagicMock()
        svc._type_repo.get = AsyncMock(return_value=activity_type)
        svc._repo = MagicMock()
        svc._repo.get_open = AsyncMock(return_value=session)

        opened = await svc.open_session(
            project_id=activity_type.project_id,
            activity_type_id=activity_type.id,
            chatroom_id=session.chatroom_id,
            subject_user_id=session.subject_user_id,
            caller_user_id=None,
        )
        assert opened is session


def _wire_session_service(
    *, subject_user_id: uuid.UUID, chatroom_id: uuid.UUID
) -> tuple[ActivitySessionService, ActivitySession]:
    session = ActivitySession(
        id=uuid.uuid4(),
        activity_type_id=uuid.uuid4(),
        chatroom_id=chatroom_id,
        subject_user_id=subject_user_id,
        status=SessionStatus.OPEN,
        created_at=_NOW,
    )
    svc = ActivitySessionService(MagicMock())
    svc._repo = MagicMock()
    svc._repo.get = AsyncMock(return_value=session)
    svc._repo.close = AsyncMock(return_value=True)
    return svc, session


class TestCloseSessionAuthz:
    async def test_closing_another_subjects_session_is_refused(self) -> None:
        """T-1: closing a session that belongs to another subject raises
        SessionNotFound and never reaches the repository close."""
        room = uuid.uuid4()
        subject_a = uuid.uuid4()
        subject_b = uuid.uuid4()
        svc, session = _wire_session_service(subject_user_id=subject_a, chatroom_id=room)

        with (
            patch.object(sess_svc.audit, "emit", new=AsyncMock()),
            pytest.raises(SessionNotFound),
        ):
            await svc.close_session(
                session_id=session.id,
                chatroom_id=room,
                subject_user_id=subject_b,  # not the session's subject
                actor_user_id=subject_b,
                actor_ip=None,
            )
        svc._repo.close.assert_not_awaited()

    async def test_subject_closes_own_session_and_double_close_is_noop(self) -> None:
        """T-4: the subject's own close succeeds, a second close is a no-op, and
        the platform-admin arm may close another subject's session."""
        room = uuid.uuid4()
        subject_a = uuid.uuid4()
        svc, session = _wire_session_service(subject_user_id=subject_a, chatroom_id=room)

        with patch.object(sess_svc.audit, "emit", new=AsyncMock()) as emit:
            await svc.close_session(
                session_id=session.id,
                chatroom_id=room,
                subject_user_id=subject_a,
                actor_user_id=subject_a,
                actor_ip=None,
            )
            svc._repo.close.assert_awaited_once()
            emit.assert_awaited_once()  # AC-4: a real close emits the audit event
            # Double close: the status='open' guard makes it 0 rows; no error, no
            # second audit for a state that did not change.
            svc._repo.close = AsyncMock(return_value=False)
            await svc.close_session(
                session_id=session.id,
                chatroom_id=room,
                subject_user_id=subject_a,
                actor_user_id=subject_a,
                actor_ip=None,
            )

        svc_admin, session_admin = _wire_session_service(subject_user_id=uuid.uuid4(), chatroom_id=room)
        with patch.object(sess_svc.audit, "emit", new=AsyncMock()):
            await svc_admin.close_session(
                session_id=session_admin.id,
                chatroom_id=room,
                subject_user_id=None,  # admin arm: no subject constraint
                actor_user_id=uuid.uuid4(),
                actor_ip=None,
            )
        svc_admin._repo.close.assert_awaited_once()

    async def test_close_in_a_different_room_is_refused(self) -> None:
        room = uuid.uuid4()
        subject_a = uuid.uuid4()
        svc, session = _wire_session_service(subject_user_id=subject_a, chatroom_id=room)

        with (
            patch.object(sess_svc.audit, "emit", new=AsyncMock()),
            pytest.raises(SessionNotFound),
        ):
            await svc.close_session(
                session_id=session.id,
                chatroom_id=uuid.uuid4(),  # wrong room
                subject_user_id=subject_a,
                actor_user_id=subject_a,
                actor_ip=None,
            )
        svc._repo.close.assert_not_awaited()


class TestSubmitSubjectAuthz:
    async def test_submitting_on_behalf_of_another_subject_is_refused(self) -> None:
        """T-3: submitting with a foreign subject raises SessionNotFound, and the
        rejection is ordered AFTER the type/project isolation check."""
        activity_type = _make_type(project_id=uuid.uuid4())
        svc, sub_repo, session = _wire_submission_service(activity_type)
        caller_b = uuid.uuid4()
        subject_a = uuid.uuid4()

        with pytest.raises(SessionNotFound):
            await svc.submit(
                project_id=activity_type.project_id,
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=caller_b,
                subject_user_id=subject_a,  # foreign subject
                caller_user_id=caller_b,
                payload={"answer": "x"},
                actor_user_id=caller_b,
                actor_ip=None,
            )
        sub_repo.insert.assert_not_awaited()

        # Ordering: a cross-tenant type still yields ActivityTypeNotFound, never the
        # subject error — the tenant boundary is checked first.
        with pytest.raises(ActivityTypeNotFound):
            await svc.submit(
                project_id=uuid.uuid4(),  # not the type's project
                activity_type_id=activity_type.id,
                chatroom_id=session.chatroom_id,
                producer_user_id=caller_b,
                subject_user_id=subject_a,
                caller_user_id=caller_b,
                payload={"answer": "x"},
                actor_user_id=caller_b,
                actor_ip=None,
            )
