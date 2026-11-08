"""The creativity judge job and its watchdog sweep (AC-14, AC-15, AC-17).

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5
(Creativity scoring) and section 7.

The facades and the provider router are faked. What these pin: the call is
billed to the judge agent's key group under ``activity_judge``; each failure
lands as one closed-vocabulary reason; a judge not bound to the room is never
called; and no outcome reaches the room, only the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks import activities as worker
from contexts.activities.domain.creativity_judge import (
    JUDGE_BAD_OUTPUT,
    JUDGE_DIMENSIONS,
    JUDGE_MISCONFIGURED,
    JUDGE_TIMEOUT,
    JUDGE_UNAVAILABLE,
    MAX_OUTPUT_TOKENS,
    SKIP_JUDGE_NOT_BOUND,
    JudgeStatus,
)
from contexts.activities.domain.models import SubmissionForJudging
from contexts.keys.application.provider_router import ProviderCallResult
from contexts.keys.domain.errors import KeyGroupExhausted

_PROJECT = uuid.uuid4()
_AGENT = uuid.uuid4()
_GROUP = uuid.uuid4()
_RUBRIC = {"flexibility": "f", "originality": "o", "convergence": "c"}


class _FakeSession:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _item(*, judge_status: str | None = "pending", scoring: bool = True) -> SubmissionForJudging:
    return SubmissionForJudging(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        project_id=_PROJECT,
        payload={"a": "red umbrella", "b": "", "c": "kite"},
        judge_status=judge_status,
        payload_schema={"properties": {"a": {}, "b": {}, "c": {}}},
        creativity_scoring={"judge_agent_id": str(_AGENT), "rubric": _RUBRIC} if scoring else None,
    )


def _agent(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": _AGENT,
        "project_id": _PROJECT,
        "model_hint": SimpleNamespace(value="claude"),
        "model_id": "judge-model",
        "key_group_id": _GROUP,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _reply(score: int = 3) -> str:
    return json.dumps({d: {"score": score, "rationale": f"{d} reason"} for d in JUDGE_DIMENSIONS})


class _Harness:
    def __init__(
        self,
        item: SubmissionForJudging | None,
        *,
        agent: SimpleNamespace | None = None,
        bound: bool = True,
        result: ProviderCallResult | BaseException | None = None,
        changed: bool = True,
    ) -> None:
        self.activities = MagicMock()
        self.activities.get_submission_for_judging = AsyncMock(return_value=item)
        self.activities.record_judgement = AsyncMock(return_value=changed)
        self.conversation = MagicMock()
        self.conversation.is_agent_in_chatroom = AsyncMock(return_value=bound)
        self.agents = MagicMock()
        self.agents.get_agent = AsyncMock(return_value=agent if agent is not None else _agent())
        self.agents.chat_model_capabilities = MagicMock(return_value={})
        self.agents.chat_model_catalog = MagicMock(return_value=())
        self.router = MagicMock()
        if isinstance(result, BaseException):
            self.router.call = AsyncMock(side_effect=result)
        else:
            self.router.call = AsyncMock(
                return_value=result or ProviderCallResult(http_status=200, body={"text": _reply()})
            )
        self.db = MagicMock()
        self.db.commit = AsyncMock()
        self.dashboard = AsyncMock()
        self.room_emit = AsyncMock()
        self.publisher = MagicMock()

    async def run(self, submission_id: uuid.UUID) -> str:
        with ExitStack() as stack:
            stack.enter_context(
                patch("shared_kernel.db.session.async_session", return_value=_FakeSession(self.db))
            )
            stack.enter_context(
                patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=self.activities)
            )
            stack.enter_context(
                patch(
                    "contexts.conversation.interfaces.facade.ConversationFacade",
                    return_value=self.conversation,
                )
            )
            stack.enter_context(
                patch("contexts.agents.interfaces.facade.AgentsFacade", return_value=self.agents)
            )
            stack.enter_context(
                patch("contexts.keys.infrastructure.adapters.build_router", return_value=self.router)
            )
            stack.enter_context(patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()))
            stack.enter_context(patch("shared_kernel.realtime.pubsub.Publisher", new=self.publisher))
            stack.enter_context(
                patch.multiple(
                    worker,
                    dispatch_dashboard_submission_validated=self.dashboard,
                    _emit_validated=self.room_emit,
                    _emit_activity_signal=AsyncMock(),
                )
            )
            return await worker.score_activity_submission({}, str(submission_id))

    def recorded(self) -> dict[str, Any]:
        self.activities.record_judgement.assert_awaited_once()
        return dict(self.activities.record_judgement.await_args.kwargs)


class TestScoring:
    async def test_a_valid_reply_is_stored_with_fluency_counted(self) -> None:
        item = _item()
        h = _Harness(item)

        assert await h.run(item.id) == "scored"

        kwargs = h.recorded()
        assert kwargs["status"] is JudgeStatus.SCORED
        assert kwargs["reason"] is None
        assert kwargs["model"] == "judge-model"
        scores = kwargs["scores"]
        assert scores["fluency"] == 2
        assert {d: scores[d] for d in JUDGE_DIMENSIONS} == dict.fromkeys(JUDGE_DIMENSIONS, 3)
        assert (scores["provider"], scores["model"]) == ("claude", "judge-model")

    async def test_the_call_is_billed_to_the_judge_agent_and_tagged(self) -> None:
        item = _item()
        h = _Harness(item)

        await h.run(item.id)

        h.router.call.assert_awaited_once()
        kwargs = h.router.call.await_args.kwargs
        assert kwargs["group_id"] == _GROUP
        request = kwargs["request"]
        assert request.usage_context == "activity_judge"
        assert (request.agent_id, request.chatroom_id) == (_AGENT, item.chatroom_id)
        assert request.payload["max_tokens"] == MAX_OUTPUT_TOKENS
        assert request.payload["temperature"] == 0
        assert "red umbrella" in request.payload["messages"][0]["content"]
        assert "red umbrella" not in request.payload["system"]

    async def test_the_outcome_reaches_the_dashboard_and_never_the_room(self) -> None:
        item = _item()
        h = _Harness(item)

        await h.run(item.id)

        h.dashboard.assert_awaited_once_with(item.chatroom_id)
        h.room_emit.assert_not_called()
        h.publisher.assert_not_called()

    async def test_the_default_model_is_used_when_the_agent_names_none(self) -> None:
        item = _item()
        h = _Harness(item, agent=_agent(model_id=None))
        h.agents.chat_model_catalog = MagicMock(
            return_value=(SimpleNamespace(provider="claude", default="catalog-default"),)
        )

        await h.run(item.id)

        assert h.router.call.await_args.kwargs["request"].payload["model"] == "catalog-default"


class TestFailures:
    @pytest.mark.parametrize(
        ("result", "reason"),
        [
            (KeyGroupExhausted(group_id=_GROUP, reason="errors"), JUDGE_UNAVAILABLE),
            (RuntimeError("socket closed"), JUDGE_UNAVAILABLE),
            (ProviderCallResult(http_status=500, body={"text": _reply()}), JUDGE_UNAVAILABLE),
            (ProviderCallResult(http_status=200, body={}), JUDGE_UNAVAILABLE),
            (ProviderCallResult(http_status=200, body={"text": "I would rather not"}), JUDGE_BAD_OUTPUT),
            # Out of range is a refusal of the scale, not something to clamp (AC-15).
            (ProviderCallResult(http_status=200, body={"text": _reply(score=9)}), JUDGE_BAD_OUTPUT),
        ],
    )
    async def test_each_failure_is_an_error_with_its_reason(self, result: Any, reason: str) -> None:
        item = _item()
        h = _Harness(item, result=result)

        assert await h.run(item.id) == "error"

        kwargs = h.recorded()
        assert (kwargs["status"], kwargs["reason"], kwargs["scores"]) == (JudgeStatus.ERROR, reason, None)
        h.db.commit.assert_awaited()

    async def test_an_agent_from_another_project_is_never_called(self) -> None:
        item = _item()
        h = _Harness(item, agent=_agent(project_id=uuid.uuid4()))

        await h.run(item.id)

        assert h.recorded()["reason"] == JUDGE_MISCONFIGURED
        h.router.call.assert_not_called()

    async def test_a_deleted_agent_is_misconfigured(self) -> None:
        item = _item()
        h = _Harness(item)
        h.agents.get_agent = AsyncMock(return_value=None)

        await h.run(item.id)

        assert h.recorded()["reason"] == JUDGE_MISCONFIGURED
        h.router.call.assert_not_called()


class TestSkipsAndNoOps:
    async def test_a_judge_not_in_the_room_is_skipped_without_a_call(self) -> None:
        item = _item()
        h = _Harness(item, bound=False)

        assert await h.run(item.id) == "skipped"

        kwargs = h.recorded()
        assert (kwargs["status"], kwargs["reason"]) == (JudgeStatus.SKIPPED, SKIP_JUDGE_NOT_BOUND)
        h.router.call.assert_not_called()
        h.conversation.is_agent_in_chatroom.assert_awaited_once_with(
            chatroom_id=item.chatroom_id, agent_id=_AGENT
        )

    @pytest.mark.parametrize("status", ["scored", "skipped", "error", None])
    async def test_a_row_that_is_not_pending_is_left_alone(self, status: str | None) -> None:
        item = _item(judge_status=status)
        h = _Harness(item)

        assert await h.run(item.id) == "not-pending"

        h.activities.record_judgement.assert_not_called()
        h.router.call.assert_not_called()

    async def test_a_missing_row_is_a_no_op(self) -> None:
        h = _Harness(None)

        assert await h.run(uuid.uuid4()) == "missing"

        h.router.call.assert_not_called()

    async def test_a_late_write_over_a_swept_row_emits_nothing(self) -> None:
        item = _item()
        h = _Harness(item, changed=False)

        assert await h.run(item.id) == "not-pending"

        h.dashboard.assert_not_called()
        # The router's usage row for the call is in this session either way.
        h.db.commit.assert_awaited_once()


class TestCancellation:
    async def test_a_timed_out_call_commits_its_usage_and_a_timeout_reason(self) -> None:
        item = _item()
        h = _Harness(item, result=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await h.run(item.id)

        kwargs = h.recorded()
        assert (kwargs["status"], kwargs["reason"], kwargs["scores"]) == (
            JudgeStatus.ERROR,
            JUDGE_TIMEOUT,
            None,
        )
        h.db.commit.assert_awaited_once()
        h.dashboard.assert_not_called()

    async def test_a_failed_timeout_write_still_propagates_the_cancel(self) -> None:
        item = _item()
        h = _Harness(item, result=asyncio.CancelledError())
        h.db.commit = AsyncMock(side_effect=RuntimeError("connection lost"))

        with pytest.raises(asyncio.CancelledError):
            await h.run(item.id)


class TestSetupFailure:
    async def test_a_lookup_that_raises_is_unavailable_not_left_pending(self) -> None:
        item = _item()
        h = _Harness(item)
        h.agents.get_agent = AsyncMock(side_effect=RuntimeError("db hiccup"))

        assert await h.run(item.id) == "error"

        assert h.recorded()["reason"] == JUDGE_UNAVAILABLE
        h.router.call.assert_not_called()


class TestWatchdogSweep:
    async def test_stalled_judging_is_swept_and_each_room_invalidated_once(self) -> None:
        room_a, room_b = uuid.uuid4(), uuid.uuid4()
        facade = MagicMock()
        facade.sweep_stalled = AsyncMock(return_value=[])
        facade.sweep_stalled_judging = AsyncMock(
            return_value=[(uuid.uuid4(), room_a), (uuid.uuid4(), room_a), (uuid.uuid4(), room_b)]
        )
        db = MagicMock()
        db.commit = AsyncMock()
        dashboard, room_emit, signal = AsyncMock(), AsyncMock(), AsyncMock()

        with (
            patch("shared_kernel.db.session.async_session", side_effect=lambda: _FakeSession(db)),
            patch("contexts.activities.interfaces.facade.ActivitiesFacade", return_value=facade),
            patch("shared_kernel.audit.flush_tail_events", new=AsyncMock()),
            patch.multiple(
                worker,
                dispatch_dashboard_submission_validated=dashboard,
                _emit_validated=room_emit,
                _emit_activity_signal=signal,
            ),
        ):
            result = await worker.activities_watchdog({})

        assert result == "swept=0 judge_swept=3"
        facade.sweep_stalled_judging.assert_awaited_once_with(ttl_seconds=worker._PENDING_TTL_SECONDS)
        assert sorted(c.args[0] for c in dashboard.await_args_list) == sorted([room_a, room_b])
        room_emit.assert_not_called()
        signal.assert_not_called()


def test_the_job_is_registered_without_retries() -> None:
    from app.workers.main import WorkerSettings

    jobs = [f for f in WorkerSettings.functions if getattr(f, "name", None) == "score_activity_submission"]
    assert len(jobs) == 1
    assert jobs[0].max_tries == 1
    assert jobs[0].timeout_s == worker.JUDGE_JOB_TIMEOUT_S
