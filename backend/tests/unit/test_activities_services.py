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

    async def test_mcp_non_uuid_agent_id_rejected(self) -> None:
        svc = ActivityTypeService(MagicMock())
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
        svc = ActivityTypeService(MagicMock())
        svc._repo = MagicMock()
        type_id = uuid.uuid4()
        svc._repo.create = AsyncMock(return_value=type_id)
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
