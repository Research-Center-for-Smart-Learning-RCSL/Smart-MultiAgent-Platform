"""Async validation worker + watchdog (AC-4, AC-8, AC-10, idempotency).

Facades are mocked; these pin: mcp/webhook write-back to validated/error, that the
webhook path goes through AgentsFacade.egress_request only, the idempotent
short-circuit on an already-terminal row, and the watchdog sweep.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.tasks import activities as worker
from contexts.activities.domain.models import (
    ActivitySubmission,
    ActivityType,
    ValidationStatus,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)


class _FakeSession:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _submission(status: ValidationStatus) -> ActivitySubmission:
    return ActivitySubmission(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        activity_type_id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        producer_user_id=uuid.uuid4(),
        payload={"answer": "x"},
        attempt_no=1,
        validation_status=status,
        is_valid=None,
        error_class=None,
        sub_scores={},
        latency_ms=None,
        retain_until=None,
        created_at=_NOW,
    )


def _type(kind: ValidatorKind, config: dict[str, Any]) -> ActivityType:
    return ActivityType(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        key="quiz",
        name="Quiz",
        payload_schema={},
        validator_kind=kind,
        validator_config=config,
        retention_days=None,
        version=1,
        created_at=_NOW,
    )


def _patches(activities_facade: MagicMock, agents_facade: MagicMock, db: MagicMock):
    return (
        patch("shared_kernel.db.session.async_session", return_value=_FakeSession(db)),
        patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=activities_facade),
        patch("contexts.agents.interfaces.facade.AgentsFacade", return_value=agents_facade),
        patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()),
        patch.object(worker, "_emit_validated", new=AsyncMock()),
    )


class TestValidateActivitySubmission:
    async def test_mcp_validated_writes_back(self) -> None:
        sub = _submission(ValidationStatus.PENDING)
        af = MagicMock()
        af.get_submission = AsyncMock(return_value=sub)
        af.get_type = AsyncMock(
            return_value=_type(
                ValidatorKind.MCP,
                {"agent_id": str(uuid.uuid4()), "binding_id": str(uuid.uuid4()), "tool_name": "score"},
            )
        )
        af.record_validation = AsyncMock(return_value=True)
        agents = MagicMock()
        agents.invoke_mcp_tool = AsyncMock(
            return_value=SimpleNamespace(
                ok=True, stdout='{"is_valid": true, "sub_scores": {"g": 90}}', stderr=""
            )
        )
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, agents, db)
        with p1, p2, p3, p4, p5:
            result = await worker.validate_activity_submission({}, str(sub.id))

        assert result == "validated"
        agents.invoke_mcp_tool.assert_awaited_once()
        verdict = af.record_validation.await_args.kwargs["result"]
        assert verdict.is_valid is True
        assert verdict.sub_scores == {"g": 90}

    async def test_webhook_goes_through_facade_only_and_records(self) -> None:
        sub = _submission(ValidationStatus.PENDING)
        af = MagicMock()
        af.get_submission = AsyncMock(return_value=sub)
        af.get_type = AsyncMock(
            return_value=_type(ValidatorKind.WEBHOOK, {"url": "https://validator.example.com/score"})
        )
        af.record_validation = AsyncMock(return_value=True)
        agents = MagicMock()
        agents.egress_request = AsyncMock(
            return_value=SimpleNamespace(
                blocked=None, status=200, body=b'{"is_valid": false, "error_class": "wrong"}'
            )
        )
        # No raw outbound: only the facade egress seam exists on the mock.
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, agents, db)
        with p1, p2, p3, p4, p5:
            result = await worker.validate_activity_submission({}, str(sub.id))

        assert result == "validated"
        agents.egress_request.assert_awaited_once()
        verdict = af.record_validation.await_args.kwargs["result"]
        assert verdict.is_valid is False
        assert verdict.error_class == "wrong"

    async def test_unavailable_validator_records_error(self) -> None:
        sub = _submission(ValidationStatus.PENDING)
        af = MagicMock()
        af.get_submission = AsyncMock(return_value=sub)
        af.get_type = AsyncMock(
            return_value=_type(
                ValidatorKind.MCP,
                {"agent_id": str(uuid.uuid4()), "binding_id": str(uuid.uuid4()), "tool_name": "s"},
            )
        )
        af.record_validation_error = AsyncMock(return_value=True)
        agents = MagicMock()
        agents.invoke_mcp_tool = AsyncMock(return_value=SimpleNamespace(ok=False, stdout="", stderr="boom"))
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, agents, db)
        with p1, p2, p3, p4, p5:
            result = await worker.validate_activity_submission({}, str(sub.id))

        assert result == "error"
        af.record_validation_error.assert_awaited_once()

    async def test_already_terminal_row_is_a_noop(self) -> None:
        sub = _submission(ValidationStatus.VALIDATED)
        af = MagicMock()
        af.get_submission = AsyncMock(return_value=sub)
        af.record_validation = AsyncMock()
        af.record_validation_error = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, MagicMock(), db)
        with p1, p2, p3, p4, p5:
            result = await worker.validate_activity_submission({}, str(sub.id))

        assert result == "not-pending"
        af.record_validation.assert_not_awaited()
        af.record_validation_error.assert_not_awaited()


class TestWatchdog:
    async def test_sweeps_stalled(self) -> None:
        af = MagicMock()
        af.sweep_stalled = AsyncMock(return_value=4)
        db = MagicMock()
        db.commit = AsyncMock()
        with (
            patch("shared_kernel.db.session.async_session", return_value=_FakeSession(db)),
            patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=af),
            patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()),
            patch("shared_kernel.audit.emit", new=AsyncMock()),
        ):
            result = await worker.activities_watchdog({})

        assert result == "swept=4"
        af.sweep_stalled.assert_awaited_once()
