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

from contexts.activities.application import submission_service as ss
from contexts.activities.application.submission_service import SubmissionService
from contexts.activities.application.type_service import ActivityTypeService
from contexts.activities.application.validators import registry
from contexts.activities.application.validators.schema import (
    payload_errors,
    validate_schema_wellformed,
)
from contexts.activities.domain.errors import (
    PayloadSchemaInvalid,
    SubmissionPayloadInvalid,
    ValidatorConfigInvalid,
)
from contexts.activities.domain.models import (
    ActivitySession,
    ActivitySubmission,
    ActivityType,
    SessionStatus,
    ValidationResult,
    ValidationStatus,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


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


class TestTypeServiceValidatorConfig:
    def teardown_method(self) -> None:
        registry.clear_registry()

    async def test_unknown_in_process_validator_id_rejected(self) -> None:
        svc = ActivityTypeService(MagicMock())
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
        svc = ActivityTypeService(MagicMock())
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

    async def test_malformed_schema_rejected_before_persist(self) -> None:
        svc = ActivityTypeService(MagicMock())
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
    svc = SubmissionService(MagicMock())
    svc._type_repo = MagicMock()
    svc._type_repo.get = AsyncMock(return_value=activity_type)
    svc._session_repo = MagicMock()
    svc._session_repo.get_open = AsyncMock(return_value=session)
    svc._session_repo.lock_for_update = AsyncMock(return_value=session)
    sub_id = uuid.uuid4()
    svc._sub_repo = MagicMock()
    svc._sub_repo.count_in_session = AsyncMock(return_value=0)
    svc._sub_repo.insert = AsyncMock(return_value=sub_id)
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
                # Client tries to forge a passing score / attempt number:
                payload={"answer": "x", "is_valid": False, "attempt_no": 99, "score": 0},
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )

        kwargs = sub_repo.insert.await_args.kwargs
        assert kwargs["validation_status"] is ValidationStatus.VALIDATED
        assert kwargs["is_valid"] is True  # from the server scorer, not the client
        assert kwargs["sub_scores"] == {"grade": 100}
        assert kwargs["attempt_no"] == 1  # server-assigned (count 0 + 1), not client's 99

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
                payload={},  # missing required 'answer'
                actor_user_id=session.subject_user_id,
                actor_ip=None,
            )
        sub_repo.insert.assert_not_awaited()
