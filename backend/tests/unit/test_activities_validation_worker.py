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
    # The completion path builds a reactive-rules signal; default it to a no-op so
    # tests focused on write-back don't have to wire it (a dedicated test asserts it).
    if not isinstance(getattr(activities_facade, "build_activity_signal", None), AsyncMock):
        activities_facade.build_activity_signal = AsyncMock(return_value=None)
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

    async def test_webhook_detail_is_parsed_onto_the_verdict(self) -> None:
        """Agent-visibility follow-up: ``result_from_json`` carries a remote
        validator's optional ``detail`` through to the verdict handed to
        ``record_validation`` — ``SubmissionService`` is what turns it into the
        persisted digest (covered in ``test_activities_services.py``)."""
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
                blocked=None,
                status=200,
                body=b'{"is_valid": true, "detail": "matched the rubric on 3/3 criteria"}',
            )
        )
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, agents, db)
        with p1, p2, p3, p4, p5:
            result = await worker.validate_activity_submission({}, str(sub.id))

        assert result == "validated"
        verdict = af.record_validation.await_args.kwargs["result"]
        assert verdict.detail == "matched the rubric on 3/3 criteria"

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


class TestActivitySignalEmit:
    """AC-1: the completion emit fires the reactive-rules signal; a redelivered
    (already-terminal) job does not re-emit."""

    async def test_completion_emits_activity_signal(self) -> None:
        sub = _submission(ValidationStatus.PENDING)
        payload = {
            "chatroom_id": str(sub.chatroom_id),
            "activity_type_key": "quiz",
            "rolling": {"same_error_count": 3},
        }
        af = MagicMock()
        af.get_submission = AsyncMock(return_value=sub)
        af.get_type = AsyncMock(
            return_value=_type(ValidatorKind.WEBHOOK, {"url": "https://validator.example.com/score"})
        )
        af.record_validation = AsyncMock(return_value=True)
        af.build_activity_signal = AsyncMock(return_value=payload)
        agents = MagicMock()
        agents.egress_request = AsyncMock(
            return_value=SimpleNamespace(blocked=None, status=200, body=b'{"is_valid": true}')
        )
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, agents, db)
        with p1, p2, p3, p4, p5, patch("shared_kernel.queue.enqueue", new=AsyncMock()) as enq:
            result = await worker.validate_activity_submission({}, str(sub.id))

        assert result == "validated"
        af.build_activity_signal.assert_awaited_once_with(submission_id=sub.id)
        enq.assert_awaited_once()
        assert enq.await_args.args[:2] == ("workflow_signal", "activity")
        assert enq.await_args.args[2] is payload

    async def test_emit_failure_does_not_fail_the_job(self) -> None:
        sub = _submission(ValidationStatus.PENDING)
        af = MagicMock()
        af.get_submission = AsyncMock(return_value=sub)
        af.get_type = AsyncMock(
            return_value=_type(ValidatorKind.WEBHOOK, {"url": "https://validator.example.com/score"})
        )
        af.record_validation = AsyncMock(return_value=True)
        af.build_activity_signal = AsyncMock(return_value={"chatroom_id": "x"})
        agents = MagicMock()
        agents.egress_request = AsyncMock(
            return_value=SimpleNamespace(blocked=None, status=200, body=b'{"is_valid": true}')
        )
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, agents, db)
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch("shared_kernel.queue.enqueue", new=AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            result = await worker.validate_activity_submission({}, str(sub.id))

        # A dropped signal must not fail a committed validation write-back.
        assert result == "validated"

    async def test_redelivery_does_not_reemit(self) -> None:
        sub = _submission(ValidationStatus.VALIDATED)  # already terminal
        af = MagicMock()
        af.get_submission = AsyncMock(return_value=sub)
        af.build_activity_signal = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()

        p1, p2, p3, p4, p5 = _patches(af, MagicMock(), db)
        with p1, p2, p3, p4, p5, patch("shared_kernel.queue.enqueue", new=AsyncMock()) as enq:
            result = await worker.validate_activity_submission({}, str(sub.id))

        assert result == "not-pending"
        af.build_activity_signal.assert_not_awaited()
        enq.assert_not_awaited()


class TestWatchdog:
    async def test_sweeps_stalled(self) -> None:
        rows = [(uuid.uuid4(), uuid.uuid4()) for _ in range(4)]
        af = MagicMock()
        af.sweep_stalled = AsyncMock(return_value=rows)
        af.build_activity_signal = AsyncMock(return_value=None)
        db = MagicMock()
        db.commit = AsyncMock()
        with (
            patch("shared_kernel.db.session.async_session", return_value=_FakeSession(db)),
            patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=af),
            patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()),
            patch("shared_kernel.audit.emit", new=AsyncMock()),
            patch.object(worker, "_emit_validated", new=AsyncMock()),
            patch("shared_kernel.queue.enqueue", new=AsyncMock()),
        ):
            result = await worker.activities_watchdog({})

        assert result == "swept=4"
        af.sweep_stalled.assert_awaited_once()

    async def test_watchdog_emits_validated_and_signal_per_swept_row(self) -> None:
        """T-5: the watchdog notifies each swept row — one activity.validated with
        status='error' on the room channel and one workflow_signal — both after the
        commit, mirroring the completion path."""
        rows = [(uuid.uuid4(), uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())]
        payload = {"submission_id": "x", "rolling": {"same_error_count": 1}}
        commit_order: list[str] = []
        af = MagicMock()
        af.sweep_stalled = AsyncMock(return_value=rows)
        af.build_activity_signal = AsyncMock(return_value=payload)
        db = MagicMock()

        async def _record_commit() -> None:
            commit_order.append("commit")

        db.commit = AsyncMock(side_effect=_record_commit)

        async def _record_emit(*_a: object, **_k: object) -> None:
            commit_order.append("emit")

        with (
            patch("shared_kernel.db.session.async_session", return_value=_FakeSession(db)),
            patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=af),
            patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()),
            patch("shared_kernel.audit.emit", new=AsyncMock()),
            patch.object(worker, "_emit_validated", new=AsyncMock(side_effect=_record_emit)) as emit_v,
            patch("shared_kernel.queue.enqueue", new=AsyncMock()) as enq,
        ):
            result = await worker.activities_watchdog({})

        assert result == "swept=2"
        # One activity.validated per row, all with status='error'.
        assert emit_v.await_count == 2
        for call in emit_v.await_args_list:
            assert call.args[2] == "error"
        # One workflow_signal("activity", …) enqueue per row.
        signal_calls = [c for c in enq.await_args_list if c.args[:2] == ("workflow_signal", "activity")]
        assert len(signal_calls) == 2
        # Every emit happens after the single commit.
        assert commit_order[0] == "commit"
        assert commit_order.count("commit") == 1
        assert set(commit_order[1:]) == {"emit"}

    async def test_watchdog_build_failure_for_one_row_does_not_drop_the_rest(self) -> None:
        rows = [(uuid.uuid4(), uuid.uuid4()), (uuid.uuid4(), uuid.uuid4())]
        af = MagicMock()
        af.sweep_stalled = AsyncMock(return_value=rows)
        # First row's signal build blows up; the second must still be emitted.
        af.build_activity_signal = AsyncMock(side_effect=[RuntimeError("db hiccup"), {"submission_id": "y"}])
        db = MagicMock()
        db.commit = AsyncMock()
        with (
            patch("shared_kernel.db.session.async_session", return_value=_FakeSession(db)),
            patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=af),
            patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()),
            patch("shared_kernel.audit.emit", new=AsyncMock()),
            patch.object(worker, "_emit_validated", new=AsyncMock()) as emit_v,
            patch("shared_kernel.queue.enqueue", new=AsyncMock()) as enq,
        ):
            result = await worker.activities_watchdog({})

        assert result == "swept=2"  # the count still reflects every swept row
        emit_v.assert_awaited_once()  # only the row whose signal built
        assert emit_v.await_args.args[:2] == (rows[1][1], rows[1][0])
        signal_calls = [c for c in enq.await_args_list if c.args[:2] == ("workflow_signal", "activity")]
        assert len(signal_calls) == 1

    async def test_watchdog_with_no_stalled_rows_emits_nothing(self) -> None:
        af = MagicMock()
        af.sweep_stalled = AsyncMock(return_value=[])
        af.build_activity_signal = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()
        with (
            patch("shared_kernel.db.session.async_session", return_value=_FakeSession(db)),
            patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=af),
            patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()),
            patch("shared_kernel.audit.emit", new=AsyncMock()) as emit_audit,
            patch.object(worker, "_emit_validated", new=AsyncMock()) as emit_v,
            patch("shared_kernel.queue.enqueue", new=AsyncMock()) as enq,
        ):
            result = await worker.activities_watchdog({})

        assert result == "swept=0"
        emit_v.assert_not_awaited()
        enq.assert_not_awaited()
        af.build_activity_signal.assert_not_awaited()
        emit_audit.assert_not_awaited()  # no aggregate audit row when nothing swept
