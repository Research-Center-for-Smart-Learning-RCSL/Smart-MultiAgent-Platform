"""Unit tests for workflow signal dispatch and event_dispatch matchers.

Covers: pure matchers (matches_message, matches_a2a, matches_a2a_trigger,
matches_variable, _regex_ok, _sender_ok), find_matching_waits,
find_run_variable_waits, workflow_event_timeout (claim/already-claimed/
not-waiting-retry/terminal), workflow_event_resume (claim/retry/terminal),
workflow_signal (message/a2a/wakeup fan-out), workflow_variable_signal,
run_triggered_workflow.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.workflow.application.event_dispatch import (
    _regex_ok,
    _sender_ok,
    matches_a2a,
    matches_a2a_trigger,
    matches_activity,
    matches_message,
    matches_variable,
)

_NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=UTC)
_ROOM = str(uuid.uuid4())
_AGENT = str(uuid.uuid4())
_RUN_ID = str(uuid.uuid4())


def _settings_with_limit(limit: int, window_s: int = 60):
    """Minimal settings stub for the F-4 trigger-budget path (limit 0 disables)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        limits=SimpleNamespace(
            workflow_trigger_per_window=limit,
            workflow_trigger_window_seconds=window_s,
        )
    )


# ===========================================================================
# Pure matchers — event_dispatch
# ===========================================================================


class TestRegexOk:
    def test_none_pattern_matches_all(self) -> None:
        assert _regex_ok(None, "anything") is True

    def test_empty_pattern_matches_all(self) -> None:
        assert _regex_ok("", "anything") is True

    def test_valid_regex_match(self) -> None:
        assert _regex_ok(r"hello\s+world", "hello   world") is True

    def test_valid_regex_no_match(self) -> None:
        assert _regex_ok(r"^exact$", "not exact") is False

    def test_invalid_regex_falls_back(self) -> None:
        assert _regex_ok(r"[invalid", "anything") is False


class TestSenderOk:
    def test_empty_matches_any(self) -> None:
        assert _sender_ok("", "user") is True

    def test_any_matches_any(self) -> None:
        assert _sender_ok("any", "agent") is True

    def test_exact_match(self) -> None:
        assert _sender_ok("user", "user") is True

    def test_mismatch(self) -> None:
        assert _sender_ok("user", "agent") is False


class TestMatchesMessage:
    def test_full_match(self) -> None:
        config = {"chatroom_id": _ROOM, "sender_filter": "user", "content_regex": r"hello"}
        assert matches_message(config, chatroom_id=_ROOM, sender_type="user", content="hello world") is True

    def test_wrong_room(self) -> None:
        config = {"chatroom_id": str(uuid.uuid4())}
        assert matches_message(config, chatroom_id=_ROOM, sender_type="user", content="x") is False

    def test_wrong_sender(self) -> None:
        config = {"chatroom_id": _ROOM, "sender_filter": "agent"}
        assert matches_message(config, chatroom_id=_ROOM, sender_type="user", content="x") is False

    def test_regex_no_match(self) -> None:
        config = {"chatroom_id": _ROOM, "content_regex": r"^goodbye$"}
        assert matches_message(config, chatroom_id=_ROOM, sender_type="user", content="hello") is False

    def test_no_sender_filter_defaults_to_any(self) -> None:
        config = {"chatroom_id": _ROOM}
        assert matches_message(config, chatroom_id=_ROOM, sender_type="agent", content="x") is True


class TestMatchesA2a:
    def test_match_no_type_filter(self) -> None:
        config = {"target_agent_id": _AGENT}
        assert matches_a2a(config, target_agent_id=_AGENT, msg_type="call") is True

    def test_match_with_type_filter(self) -> None:
        config = {"target_agent_id": _AGENT, "types": ["call", "notify"]}
        assert matches_a2a(config, target_agent_id=_AGENT, msg_type="call") is True

    def test_wrong_agent(self) -> None:
        config = {"target_agent_id": str(uuid.uuid4())}
        assert matches_a2a(config, target_agent_id=_AGENT, msg_type="call") is False

    def test_type_not_in_list(self) -> None:
        config = {"target_agent_id": _AGENT, "types": ["notify"]}
        assert matches_a2a(config, target_agent_id=_AGENT, msg_type="call") is False


class TestMatchesA2aTrigger:
    def test_match(self) -> None:
        config = {"agent_id": _AGENT, "event_types": ["call", "instruct"]}
        assert matches_a2a_trigger(config, agent_id=_AGENT, msg_type="call") is True

    def test_wrong_agent(self) -> None:
        config = {"agent_id": str(uuid.uuid4()), "event_types": ["call"]}
        assert matches_a2a_trigger(config, agent_id=_AGENT, msg_type="call") is False

    def test_type_not_in_list(self) -> None:
        config = {"agent_id": _AGENT, "event_types": ["notify"]}
        assert matches_a2a_trigger(config, agent_id=_AGENT, msg_type="call") is False

    def test_empty_event_types(self) -> None:
        config = {"agent_id": _AGENT, "event_types": []}
        assert matches_a2a_trigger(config, agent_id=_AGENT, msg_type="call") is False


class TestMatchesActivity:
    def _m(self, config: dict, *, key: str = "quiz", status: str = "validated") -> bool:
        return matches_activity(config, chatroom_id=_ROOM, activity_type_key=key, validation_status=status)

    def test_room_only_match(self) -> None:
        assert self._m({"chatroom_id": _ROOM}) is True

    def test_wrong_room(self) -> None:
        assert self._m({"chatroom_id": str(uuid.uuid4())}) is False

    def test_single_key_match(self) -> None:
        assert self._m({"chatroom_id": _ROOM, "activity_type_key": "quiz"}) is True

    def test_single_key_mismatch(self) -> None:
        assert self._m({"chatroom_id": _ROOM, "activity_type_key": "poll"}) is False

    def test_allowed_list_match(self) -> None:
        assert self._m({"chatroom_id": _ROOM, "activity_type_keys": ["poll", "quiz"]}) is True

    def test_allowed_list_mismatch(self) -> None:
        assert self._m({"chatroom_id": _ROOM, "activity_type_keys": ["poll", "survey"]}) is False

    def test_malformed_keys_string_is_ignored_not_char_expanded(self) -> None:
        # A stored config with a string (not a list) must not expand to a
        # per-character allow-list. With the isinstance guard the malformed field
        # is ignored (behaves as no filter), so a full-word key matches — whereas
        # char-expansion would have rejected "quiz" (not in ['q','u','i','z']).
        assert self._m({"chatroom_id": _ROOM, "activity_type_keys": "quiz"}, key="quiz") is True

    def test_status_filter_match(self) -> None:
        cfg = {"chatroom_id": _ROOM, "validation_status": "validated"}
        assert self._m(cfg, status="validated") is True

    def test_status_filter_rejects_other_phase(self) -> None:
        cfg = {"chatroom_id": _ROOM, "validation_status": "validated"}
        assert self._m(cfg, status="pending") is False

    def test_status_any_matches_every_phase(self) -> None:
        cfg = {"chatroom_id": _ROOM, "validation_status": "any"}
        assert self._m(cfg, status="pending") is True
        assert self._m(cfg, status="error") is True

    def test_status_absent_matches_every_phase(self) -> None:
        assert self._m({"chatroom_id": _ROOM}, status="pending") is True


class TestActivityRollingSel:
    """AC-3: an impasse gate is an SEL condition over the rolling aggregate the
    core precomputes into the signal payload (edge guards are never evaluated)."""

    def _eval(self, count: object) -> bool:
        from contexts.workflow.sel.evaluator import evaluate

        # SEL surface syntax delimits variable refs with {{ }} (see test_sel_evaluator);
        # the numeric compare then holds because int() coerces and _safe_cmp needs both sides numeric.
        scope = {"__trigger__": {"rolling": {"same_error_count": count}}}
        return bool(evaluate("int({{ trigger.rolling.same_error_count }}) >= 3", scope))

    def test_threshold_met(self) -> None:
        assert self._eval(3) is True

    def test_below_threshold(self) -> None:
        assert self._eval(2) is False

    def test_string_count_coerced(self) -> None:
        assert self._eval("3") is True


class TestMatchesVariable:
    @patch("contexts.workflow.sel.evaluator.evaluate", return_value=True)
    def test_expression_true(self, _eval) -> None:
        config = {"expression": "x > 0"}
        assert matches_variable(config, {"x": 1}) is True
        _eval.assert_called_once_with("x > 0", {"x": 1})

    @patch("contexts.workflow.sel.evaluator.evaluate", return_value=False)
    def test_expression_false(self, _eval) -> None:
        config = {"expression": "x > 0"}
        assert matches_variable(config, {"x": -1}) is False

    def test_empty_expression(self) -> None:
        assert matches_variable({}, {"x": 1}) is False
        assert matches_variable({"expression": ""}, {"x": 1}) is False

    @patch("contexts.workflow.sel.evaluator.evaluate", side_effect=Exception("parse error"))
    def test_eval_error_returns_false(self, _eval) -> None:
        config = {"expression": "bad()"}
        assert matches_variable(config, {}) is False


# ===========================================================================
# find_matching_waits / find_run_variable_waits
# ===========================================================================


class TestFindMatchingWaits:
    async def test_finds_matching_waits(self) -> None:
        from contexts.workflow.application.event_dispatch import find_matching_waits

        redis = AsyncMock()
        run_id, node_id = str(uuid.uuid4()), "n1"
        redis.smembers.return_value = [f"{run_id}:{node_id}".encode()]
        payload = json.dumps({"match": {"chatroom_id": _ROOM}})
        redis.get.return_value = payload

        results = await find_matching_waits(redis, "message_in_room", lambda m: True)

        assert results == [(run_id, node_id)]

    async def test_skips_expired_claims(self) -> None:
        from contexts.workflow.application.event_dispatch import find_matching_waits

        redis = AsyncMock()
        redis.smembers.return_value = [b"run1:n1"]
        redis.get.return_value = None

        results = await find_matching_waits(redis, "message_in_room", lambda m: True)

        assert results == []
        redis.srem.assert_awaited_once()

    async def test_skips_non_matching(self) -> None:
        from contexts.workflow.application.event_dispatch import find_matching_waits

        redis = AsyncMock()
        redis.smembers.return_value = [b"run1:n1"]
        redis.get.return_value = json.dumps({"match": {}})

        results = await find_matching_waits(redis, "x", lambda m: False)

        assert results == []

    async def test_skips_malformed_member(self) -> None:
        from contexts.workflow.application.event_dispatch import find_matching_waits

        redis = AsyncMock()
        redis.smembers.return_value = [b"no_colon"]
        redis.get.return_value = None

        results = await find_matching_waits(redis, "x", lambda m: True)

        assert results == []


class TestFindRunVariableWaits:
    async def test_finds_waits_for_run(self) -> None:
        from contexts.workflow.application.event_dispatch import find_run_variable_waits

        redis = AsyncMock()
        run_id = str(uuid.uuid4())
        redis.smembers.return_value = [
            f"{run_id}:n1".encode(),
            f"{uuid.uuid4()}:n2".encode(),
        ]
        redis.get.return_value = json.dumps({"match": {"expression": "x > 0"}})

        results = await find_run_variable_waits(redis, run_id)

        assert len(results) == 1
        assert results[0][0] == run_id
        assert results[0][1] == "n1"
        assert results[0][2] == {"expression": "x > 0"}


# ===========================================================================
# workflow_signals tasks
# ===========================================================================


class TestWorkflowEventTimeout:
    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_already_claimed_returns_early(self, mock_redis_fn, _session) -> None:
        from app.workers.tasks.workflow_signals import workflow_event_timeout

        redis = AsyncMock()
        redis.ttl.return_value = 60
        redis.getdel.return_value = None
        mock_redis_fn.return_value = redis

        result = await workflow_event_timeout({}, _RUN_ID, "n1")

        assert result == "already_received"

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_timeout_resumes_and_dispatches(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_event_timeout

        redis = AsyncMock()
        redis.ttl.return_value = 60
        redis.getdel.return_value = json.dumps({"event_type": "timer"}).encode()
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        engine = AsyncMock()
        engine.resume_at_port.return_value = True

        with patch(
            "contexts.workflow.application.run_engine.RunEngine",
            return_value=engine,
        ):
            result = await workflow_event_timeout({"redis": AsyncMock()}, _RUN_ID, "n1")

        assert result == "timed_out"
        engine.resume_at_port.assert_awaited_once()
        call_args = engine.resume_at_port.call_args
        assert call_args[0][2] == "timeout"

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_not_waiting_retries(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_event_timeout

        redis = AsyncMock()
        redis.ttl.return_value = 60
        redis.getdel.return_value = b'{"event_type": "timer"}'
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        engine = AsyncMock()
        engine.resume_at_port.return_value = False

        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.application.run_engine.RunEngine",
                return_value=engine,
            ),
            patch(
                "app.workers.tasks.workflow_signals._run_is_terminal",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "app.workers.tasks.workflow_signals._restore_claim",
                new_callable=AsyncMock,
            ) as restore,
        ):
            result = await workflow_event_timeout({"redis": pool}, _RUN_ID, "n1", attempt=0)

        assert result == "not_waiting:retry"
        restore.assert_awaited_once()
        pool.enqueue_job.assert_awaited_once()

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_terminal_run_returns_noop(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_event_timeout

        redis = AsyncMock()
        redis.ttl.return_value = 60
        redis.getdel.return_value = b'{"event_type": "timer"}'
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        engine = AsyncMock()
        engine.resume_at_port.return_value = False

        with (
            patch(
                "contexts.workflow.application.run_engine.RunEngine",
                return_value=engine,
            ),
            patch(
                "app.workers.tasks.workflow_signals._run_is_terminal",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await workflow_event_timeout({"redis": AsyncMock()}, _RUN_ID, "n1")

        assert result == "noop:terminal"


class TestWorkflowEventResume:
    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_already_claimed(self, mock_redis_fn, _session) -> None:
        from app.workers.tasks.workflow_signals import workflow_event_resume

        redis = AsyncMock()
        redis.ttl.return_value = 60
        redis.getdel.return_value = None
        mock_redis_fn.return_value = redis

        result = await workflow_event_resume({}, _RUN_ID, "n1")

        assert result == "already_claimed"

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_resume_success(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_event_resume

        redis = AsyncMock()
        redis.ttl.return_value = 120
        redis.getdel.return_value = json.dumps({"event_type": "message_in_room"}).encode()
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        engine = AsyncMock()
        engine.resume_at_port.return_value = True
        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.application.run_engine.RunEngine",
                return_value=engine,
            ),
            patch("shared_kernel.audit.emit", new_callable=AsyncMock),
        ):
            result = await workflow_event_resume({"redis": pool}, _RUN_ID, "n1")

        assert result == "resumed"
        engine.resume_at_port.assert_awaited_once()
        assert engine.resume_at_port.call_args[0][2] == "default"


class TestWorkflowSignal:
    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_message_signal_fans_out(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.application.event_dispatch.find_matching_waits",
                new_callable=AsyncMock,
                return_value=[(_RUN_ID, "n1")],
            ),
            patch(
                "contexts.workflow.application.event_dispatch.find_triggered_workflows",
                new_callable=AsyncMock,
                return_value=[uuid.uuid4()],
            ),
        ):
            result = await workflow_signal(
                {"redis": pool},
                "message",
                {"chatroom_id": _ROOM, "sender_type": "user", "content": "hi"},
            )

        assert "resumed=1" in result
        assert "triggered=1" in result
        assert pool.enqueue_job.await_count == 2

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_activity_signal_fans_out(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.application.event_dispatch.find_matching_waits",
                new_callable=AsyncMock,
                return_value=[(_RUN_ID, "n1")],
            ),
            patch(
                "contexts.workflow.application.event_dispatch.find_triggered_workflows",
                new_callable=AsyncMock,
                return_value=[uuid.uuid4()],
            ) as find_trig,
        ):
            result = await workflow_signal(
                {"redis": pool},
                "activity",
                {
                    "chatroom_id": _ROOM,
                    "activity_type_key": "quiz",
                    "rolling": {"same_error_count": 3},
                },
            )

        assert "resumed=1" in result
        assert "triggered=1" in result
        assert pool.enqueue_job.await_count == 2
        # Triggers are scanned for the activity_event kind specifically.
        assert find_trig.await_args.args[1] == "activity_event"
        # The started run carries the trigger payload (rolling aggregate for SEL).
        trig_call = next(c for c in pool.enqueue_job.await_args_list if c.args[0] == "run_triggered_workflow")
        assert trig_call.args[2]["trigger_type"] == "activity_event"
        assert trig_call.args[2]["rolling"] == {"same_error_count": 3}

    @patch("shared_kernel.auth.clients.get_redis")
    async def test_a2a_signal(self, mock_redis_fn) -> None:
        from app.workers.tasks.workflow_signals import workflow_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis
        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.application.event_dispatch.find_matching_waits",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("shared_kernel.db.session.async_session") as mock_sc,
            patch(
                "contexts.workflow.application.event_dispatch.find_triggered_workflows",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            db = AsyncMock()
            mock_sc.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_sc.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await workflow_signal(
                {"redis": pool},
                "a2a",
                {"target_agent_id": _AGENT, "msg_type": "call"},
            )

        assert "resumed=0" in result

    @patch("shared_kernel.auth.clients.get_redis")
    async def test_a2a_signal_skips_a_workflow_already_on_the_trigger_path(self, mock_redis_fn) -> None:
        # F-4/AC-4: a workflow whose own earlier run produced this envelope is on
        # the inbound trigger_path; the dispatch must NOT start it again. AC-7:
        # exercised for both the call and instruct edges of the cycle.
        from app.workers.tasks.workflow_signals import workflow_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis
        wf_id = uuid.uuid4()

        for msg_type in ("call", "instruct"):
            pool = AsyncMock()
            with (
                patch(
                    "contexts.workflow.application.event_dispatch.find_matching_waits",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch("shared_kernel.db.session.async_session") as mock_sc,
                patch(
                    "contexts.workflow.application.event_dispatch.find_triggered_workflows",
                    new_callable=AsyncMock,
                    return_value=[wf_id],
                ),
            ):
                db = AsyncMock()
                mock_sc.return_value.__aenter__ = AsyncMock(return_value=db)
                mock_sc.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await workflow_signal(
                    {"redis": pool},
                    "a2a",
                    {
                        "target_agent_id": _AGENT,
                        "msg_type": msg_type,
                        "trigger_depth": 1,
                        "trigger_path": [str(wf_id)],
                    },
                )

            assert "triggered=0" in result, msg_type
            # No run was started for the workflow already on the chain.
            trig_calls = [
                c for c in pool.enqueue_job.await_args_list if c.args[0] == "run_triggered_workflow"
            ]
            assert trig_calls == [], msg_type

    @patch("shared_kernel.auth.clients.get_redis")
    async def test_a2a_signal_starts_a_workflow_not_on_the_trigger_path(self, mock_redis_fn) -> None:
        # The guard is a skip, never an expansion: a workflow NOT on the chain
        # still starts, and its run carries the inbound chain forward.
        from app.workers.tasks.workflow_signals import workflow_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis
        wf_id = uuid.uuid4()
        other = str(uuid.uuid4())
        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.application.event_dispatch.find_matching_waits",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("shared_kernel.db.session.async_session") as mock_sc,
            patch(
                "contexts.workflow.application.event_dispatch.find_triggered_workflows",
                new_callable=AsyncMock,
                return_value=[wf_id],
            ),
        ):
            db = AsyncMock()
            mock_sc.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_sc.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await workflow_signal(
                {"redis": pool},
                "a2a",
                {
                    "target_agent_id": _AGENT,
                    "msg_type": "call",
                    "trigger_depth": 1,
                    "trigger_path": [other],
                },
            )

        assert "triggered=1" in result
        trig_call = next(c for c in pool.enqueue_job.await_args_list if c.args[0] == "run_triggered_workflow")
        # The started run carries the inbound chain so its executor can append.
        assert trig_call.args[2]["trigger_path"] == [other]

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_wakeup_signal(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        wf_id = uuid.uuid4()
        pool = AsyncMock()

        with patch(
            "contexts.workflow.application.event_dispatch.find_triggered_workflows",
            new_callable=AsyncMock,
            return_value=[wf_id],
        ):
            result = await workflow_signal(
                {"redis": pool},
                "wakeup",
                {"agent_id": _AGENT},
            )

        assert "triggered=1" in result
        pool.enqueue_job.assert_awaited_once()
        call_args = pool.enqueue_job.call_args
        assert call_args[0][0] == "run_triggered_workflow"


class TestWorkflowVariableSignal:
    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_resumes_matching_variable_waits(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_variable_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        run_id = str(uuid.uuid4())
        run = MagicMock()
        run.variables = {"x": 10}
        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowRunRepository.get",
                new_callable=AsyncMock,
                return_value=run,
            ),
            patch(
                "contexts.workflow.application.event_dispatch.find_run_variable_waits",
                new_callable=AsyncMock,
                return_value=[(run_id, "n1", {"expression": "x > 5"})],
            ),
            patch(
                "contexts.workflow.application.event_dispatch.matches_variable",
                return_value=True,
            ),
        ):
            result = await workflow_variable_signal({"redis": pool}, run_id, "sv1")

        assert "resumed=1" in result
        pool.enqueue_job.assert_awaited_once()

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_no_match_no_resume(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import workflow_variable_signal

        redis = AsyncMock()
        mock_redis_fn.return_value = redis

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        run_id = str(uuid.uuid4())
        run = MagicMock()
        run.variables = {"x": 0}
        pool = AsyncMock()

        with (
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowRunRepository.get",
                new_callable=AsyncMock,
                return_value=run,
            ),
            patch(
                "contexts.workflow.application.event_dispatch.find_run_variable_waits",
                new_callable=AsyncMock,
                return_value=[(run_id, "n1", {"expression": "x > 5"})],
            ),
            patch(
                "contexts.workflow.application.event_dispatch.matches_variable",
                return_value=False,
            ),
        ):
            result = await workflow_variable_signal({"redis": pool}, run_id, "sv1")

        assert "resumed=0" in result
        pool.enqueue_job.assert_not_awaited()


class TestRunTriggeredWorkflow:
    @patch("shared_kernel.db.session.async_session")
    async def test_trigger_success(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import run_triggered_workflow

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        wf_id = str(uuid.uuid4())
        run_id = uuid.uuid4()
        svc = AsyncMock()
        svc.trigger_run.return_value = run_id
        pool = AsyncMock()

        # F-4: the budget path runs first. Disable it here (limit 0) so this test
        # isolates the trigger_run success path; the budget has its own tests.
        with (
            patch("shared_kernel.auth.clients.get_redis", return_value=AsyncMock()),
            patch("app.config.settings.get_settings", return_value=_settings_with_limit(0)),
            patch(
                "contexts.workflow.application.workflow_service.WorkflowService",
                return_value=svc,
            ),
        ):
            result = await run_triggered_workflow(
                {"redis": pool}, wf_id, {"trigger_type": "message_received"}
            )

        assert result == str(run_id)
        svc.trigger_run.assert_awaited_once()
        db.commit.assert_awaited_once()

    @patch("shared_kernel.db.session.async_session")
    async def test_trigger_error(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_signals import run_triggered_workflow

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        svc = AsyncMock()
        svc.trigger_run.side_effect = RuntimeError("workflow deleted")

        with (
            patch("shared_kernel.auth.clients.get_redis", return_value=AsyncMock()),
            patch("app.config.settings.get_settings", return_value=_settings_with_limit(0)),
            patch(
                "contexts.workflow.application.workflow_service.WorkflowService",
                return_value=svc,
            ),
        ):
            result = await run_triggered_workflow({}, str(uuid.uuid4()))

        assert result == "error"
