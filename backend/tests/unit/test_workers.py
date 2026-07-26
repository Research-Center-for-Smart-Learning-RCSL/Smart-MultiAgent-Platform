"""Unit tests for Arq worker tasks.

Covers: retention sweep (coordinator + individual policies), workflow_cron
scheduling logic, workflow_watchdog timeout detection, workflow_steps task
dispatch, and workflow_common helpers.

All infrastructure (DB sessions, Redis, MinIO, Arq pool) is mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ===========================================================================
# retention — individual policies
# ===========================================================================


class TestRetentionPolicies:
    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_expire_invites(self, _audit) -> None:
        from app.workers.tasks.retention import _expire_invites

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 5
        session.execute.return_value = result

        count = await _expire_invites(session)

        assert count == 5
        session.execute.assert_awaited_once()

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_expire_oc_transfers(self, _audit) -> None:
        from app.workers.tasks.retention import _expire_oc_transfers

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 2
        session.execute.return_value = result

        count = await _expire_oc_transfers(session)

        assert count == 2

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_expire_approvals(self, _audit) -> None:
        from app.workers.tasks.retention import _expire_approvals

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 0
        session.execute.return_value = result

        count = await _expire_approvals(session)

        assert count == 0

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_purge_expired_tokens(self, _audit) -> None:
        from app.workers.tasks.retention import _TOKEN_TABLES, _purge_expired_tokens

        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 10
        session.execute.return_value = result

        count = await _purge_expired_tokens(session)

        assert count == 10 * len(_TOKEN_TABLES)

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    @patch("app.workers.tasks.retention.now")
    async def test_prune_idle_sessions(self, mock_now, _audit) -> None:
        from app.workers.tasks.retention import _prune_idle_sessions

        mock_now.return_value = datetime(2026, 6, 22, tzinfo=UTC)
        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 7
        session.execute.return_value = result

        count = await _prune_idle_sessions(session)

        assert count == 7

    @patch("app.workers.tasks.retention.audit.emit", new_callable=AsyncMock)
    async def test_emit_summary(self, mock_audit) -> None:
        from app.workers.tasks.retention import _emit_summary

        session = AsyncMock()
        await _emit_summary(session, "test.action", 42)

        mock_audit.assert_awaited_once()
        call_args = mock_audit.call_args
        event = call_args.args[1]
        assert event.action == "test.action"
        assert event.metadata["rows_affected"] == 42


# ===========================================================================
# retention — sweep coordinator
# ===========================================================================


class TestRetentionSweep:
    @patch("app.workers.tasks.retention.get_sessionmaker")
    @patch("app.workers.tasks.retention.RETENTION_LAST_RUN_TIMESTAMP")
    @patch("app.workers.tasks.retention.RETENTION_LAST_ROWS")
    @patch("app.workers.tasks.retention.RETENTION_FAILURES")
    async def test_sweep_runs_all_policies(self, _failures, _rows, _ts, mock_sm) -> None:
        from app.workers.tasks.retention import retention_sweep

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
        mock_sm.return_value = MagicMock(return_value=session)

        # Mock all policy functions to return 0
        with patch("app.workers.tasks.retention._POLICIES") as mock_policies:
            policy1 = AsyncMock(return_value=1)
            policy2 = AsyncMock(return_value=2)
            mock_policies.__iter__ = MagicMock(return_value=iter([("p1", policy1), ("p2", policy2)]))

            report = await retention_sweep({})

        assert report == {"p1": 1, "p2": 2}

    @patch("app.workers.tasks.retention.get_sessionmaker")
    @patch("app.workers.tasks.retention.RETENTION_LAST_RUN_TIMESTAMP")
    @patch("app.workers.tasks.retention.RETENTION_LAST_ROWS")
    @patch("app.workers.tasks.retention.RETENTION_FAILURES")
    async def test_sweep_handles_policy_failure(self, _failures, _rows, _ts, mock_sm) -> None:
        from app.workers.tasks.retention import retention_sweep

        session = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))
        mock_sm.return_value = MagicMock(return_value=session)

        boom = AsyncMock(side_effect=RuntimeError("db down"))

        with patch("app.workers.tasks.retention._POLICIES") as mock_policies:
            mock_policies.__iter__ = MagicMock(return_value=iter([("broken", boom)]))

            report = await retention_sweep({})

        assert report["broken"] == -1


# ===========================================================================
# workflow_common — helpers
# ===========================================================================


class TestWorkflowCommon:
    async def test_run_is_terminal_succeeded(self) -> None:
        from app.workers.tasks.workflow_common import _run_is_terminal
        from contexts.workflow.domain.models import RunState

        db = AsyncMock()
        run = MagicMock()
        run.state = RunState.SUCCEEDED

        with patch(
            "contexts.workflow.infrastructure.repositories.WorkflowRunRepository.get",
            new_callable=AsyncMock,
            return_value=run,
        ):
            result = await _run_is_terminal(db, str(uuid.uuid4()))

        assert result is True

    async def test_run_is_terminal_running(self) -> None:
        from app.workers.tasks.workflow_common import _run_is_terminal
        from contexts.workflow.domain.models import RunState

        db = AsyncMock()
        run = MagicMock()
        run.state = RunState.RUNNING

        with patch(
            "contexts.workflow.infrastructure.repositories.WorkflowRunRepository.get",
            new_callable=AsyncMock,
            return_value=run,
        ):
            result = await _run_is_terminal(db, str(uuid.uuid4()))

        assert result is False

    async def test_run_is_terminal_none(self) -> None:
        from app.workers.tasks.workflow_common import _run_is_terminal

        db = AsyncMock()

        with patch(
            "contexts.workflow.infrastructure.repositories.WorkflowRunRepository.get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _run_is_terminal(db, str(uuid.uuid4()))

        assert result is True

    async def test_restore_claim_floors_to_min_ttl(self) -> None:
        # F-32: a decayed original TTL must not be written back verbatim when it
        # is shorter than the remaining consumer budget — the restore floors to
        # the larger of (remaining key TTL, min_ttl).
        from app.workers.tasks.workflow_common import _restore_claim

        redis = AsyncMock()
        await _restore_claim(redis, "wf:wait:x:n1", b'{"data": 1}', 120, min_ttl=600)

        redis.set.assert_awaited_once_with("wf:wait:x:n1", b'{"data": 1}', ex=600)

    async def test_restore_claim_keeps_longer_original_ttl(self) -> None:
        # When the original TTL already outlives the budget, keep it — the floor
        # only raises, never shortens.
        from app.workers.tasks.workflow_common import _restore_claim

        redis = AsyncMock()
        await _restore_claim(redis, "key", b"data", 900, min_ttl=600)

        redis.set.assert_awaited_once_with("key", b"data", ex=900)

    async def test_restore_claim_default_ttl_covers_budget(self) -> None:
        # The default floor is the full retry budget, no longer a 60s fallback
        # shorter than the budget it guards (the F-32 bug in miniature).
        from app.workers.tasks.workflow_common import (
            _CLAIM_RESTORE_TTL_S,
            _RESUME_RETRY_DELAY_S,
            _RESUME_RETRY_MAX_ATTEMPTS,
            _restore_claim,
        )

        redis = AsyncMock()
        await _restore_claim(redis, "key", b"data", 0)

        redis.set.assert_awaited_once_with("key", b"data", ex=_CLAIM_RESTORE_TTL_S)
        assert _CLAIM_RESTORE_TTL_S >= _RESUME_RETRY_MAX_ATTEMPTS * _RESUME_RETRY_DELAY_S

    async def test_restore_claim_reindexes_when_asked(self) -> None:
        # F-37 (b): the restore is not the inverse of the claim unless it also
        # re-adds the by-event index member the claim protocol removed.
        from app.workers.tasks.workflow_common import _restore_claim

        redis = AsyncMock()
        await _restore_claim(
            redis, "wf:wait:r:n1", b"data", 60, reindex=("wf:wait:by_event:timer", "r:n1")
        )

        redis.sadd.assert_awaited_once_with("wf:wait:by_event:timer", "r:n1")

    async def test_restore_claim_omits_reindex_by_default(self) -> None:
        # The approval and instruct callers pass no reindex and have no index
        # to corrupt — must compile and behave unchanged.
        from app.workers.tasks.workflow_common import _restore_claim

        redis = AsyncMock()
        await _restore_claim(redis, "wf:approval:x", b"data", 60)

        redis.sadd.assert_not_awaited()

    async def test_emit_resumed(self) -> None:
        from app.workers.tasks.workflow_common import _emit_resumed

        db = AsyncMock()
        run_id = str(uuid.uuid4())

        with patch("shared_kernel.audit.emit", new_callable=AsyncMock) as mock_audit:
            await _emit_resumed(db, run_id, "n1", reason="test")

        mock_audit.assert_awaited_once()
        event = mock_audit.call_args.args[1]
        assert event.action == "workflow.resumed"
        assert event.metadata["node_id"] == "n1"


# ===========================================================================
# workflow_steps — task dispatch
# ===========================================================================


class TestWorkflowSteps:
    @patch("shared_kernel.db.session.async_session")
    async def test_run_workflow_step(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_steps import run_workflow_step

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        engine = AsyncMock()

        with patch(
            "contexts.workflow.application.run_engine.RunEngine",
            return_value=engine,
        ):
            result = await run_workflow_step(
                {"redis": AsyncMock()},
                run_id=str(uuid.uuid4()),
                node_id="n1",
                from_edge="e1",
            )

        assert result == "ok"
        engine.run_step.assert_awaited_once()
        db.commit.assert_awaited_once()
        engine.dispatch_enqueues.assert_awaited_once()

    @patch("shared_kernel.db.session.async_session")
    async def test_retry_workflow_node(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_steps import retry_workflow_node

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        engine = AsyncMock()

        with patch(
            "contexts.workflow.application.run_engine.RunEngine",
            return_value=engine,
        ):
            result = await retry_workflow_node(
                {"redis": AsyncMock()},
                run_id=str(uuid.uuid4()),
                node_id="n1",
            )

        assert result == "ok"
        engine.retry_node.assert_awaited_once()

    @patch("shared_kernel.db.session.async_session")
    async def test_subagent_timeout_fails_waiting_run(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_steps import workflow_subagent_timeout
        from contexts.workflow.domain.models import RunState

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        engine = AsyncMock()
        run = MagicMock()
        run.state = RunState.WAITING
        engine._runs.get.return_value = run
        engine.force_fail.return_value = True

        with patch(
            "contexts.workflow.application.run_engine.RunEngine",
            return_value=engine,
        ):
            result = await workflow_subagent_timeout(
                {},
                run_id=str(uuid.uuid4()),
                node_id="n1",
            )

        assert result == "timed_out"
        engine.force_fail.assert_awaited_once()
        db.commit.assert_awaited_once()

    @patch("shared_kernel.db.session.async_session")
    async def test_subagent_complete_resumes(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_steps import workflow_subagent_complete

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        engine = AsyncMock()

        with patch(
            "contexts.workflow.application.run_engine.RunEngine",
            return_value=engine,
        ):
            result = await workflow_subagent_complete(
                {"redis": AsyncMock()},
                run_id=str(uuid.uuid4()),
                node_id="n1",
                port="success",
            )

        assert result == "resumed"
        engine.resume_at_port.assert_awaited_once()


# ===========================================================================
# workflow_watchdog — timeout detection
# ===========================================================================


class TestWorkflowWatchdog:
    @patch("shared_kernel.db.session.async_session")
    async def test_watchdog_fails_expired_run(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_watchdog import workflow_watchdog

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        run_id = uuid.uuid4()
        wf_id = uuid.uuid4()
        started_at = datetime(2026, 6, 21, 0, 0, 0, tzinfo=UTC)  # > 24 hours ago

        runs_repo = AsyncMock()
        runs_repo.list_active.return_value = [(run_id, wf_id, started_at)]
        steps_repo = AsyncMock()
        wf_repo = AsyncMock()
        wf = MagicMock()
        wf.definition = {"timeouts": {"run_max_seconds": 3600}}
        wf_repo.get.return_value = wf
        engine = AsyncMock()
        engine.force_fail.return_value = True

        with (
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowRunRepository",
                return_value=runs_repo,
            ),
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowStepRepository",
                return_value=steps_repo,
            ),
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowRepository",
                return_value=wf_repo,
            ),
            patch(
                "contexts.workflow.application.run_engine.RunEngine",
                return_value=engine,
            ),
        ):
            result = await workflow_watchdog({})

        assert "failed=1" in result
        engine.force_fail.assert_awaited_once()
        engine.dispatch_enqueues.assert_awaited_once_with(None)

    @patch("shared_kernel.db.session.async_session")
    async def test_watchdog_skips_healthy_run(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_watchdog import workflow_watchdog

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        run_id = uuid.uuid4()
        wf_id = uuid.uuid4()
        started_at = datetime.now(UTC) - timedelta(seconds=10)

        runs_repo = AsyncMock()
        runs_repo.list_active.return_value = [(run_id, wf_id, started_at)]
        steps_repo = AsyncMock()
        steps_repo.latest_activity_at.return_value = datetime.now(UTC) - timedelta(seconds=5)
        wf_repo = AsyncMock()
        wf = MagicMock()
        wf.definition = {"timeouts": {"run_max_seconds": 3600, "idle_max_seconds": 1800}}
        wf_repo.get.return_value = wf
        engine = AsyncMock()

        with (
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowRunRepository",
                return_value=runs_repo,
            ),
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowStepRepository",
                return_value=steps_repo,
            ),
            patch(
                "contexts.workflow.infrastructure.repositories.WorkflowRepository",
                return_value=wf_repo,
            ),
            patch(
                "contexts.workflow.application.run_engine.RunEngine",
                return_value=engine,
            ),
        ):
            result = await workflow_watchdog({})

        assert "failed=0" in result
        engine.force_fail.assert_not_awaited()

    @patch("shared_kernel.db.session.async_session")
    async def test_watchdog_no_active_runs(self, mock_session_cm) -> None:
        from app.workers.tasks.workflow_watchdog import workflow_watchdog

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        runs_repo = AsyncMock()
        runs_repo.list_active.return_value = []

        with patch(
            "contexts.workflow.infrastructure.repositories.WorkflowRunRepository",
            return_value=runs_repo,
        ):
            result = await workflow_watchdog({})

        assert "failed=0" in result


# ===========================================================================
# workflow_cron — fire logic
# ===========================================================================


class TestWorkflowCron:
    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_cron_fires_due_workflow(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_cron import workflow_cron_scheduler

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        wf_id = uuid.uuid4()
        row = MagicMock()
        row.id = wf_id
        row.definition = {
            "nodes": [
                {"type": "trigger", "config": {"trigger_type": "cron", "cron_expression": "* * * * *"}},
            ],
        }
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[row]))
        redis = AsyncMock()
        redis.get.return_value = None  # no last_fire
        mock_redis_fn.return_value = redis

        svc = AsyncMock()
        with patch(
            "contexts.workflow.application.workflow_service.WorkflowService",
            return_value=svc,
        ):
            result = await workflow_cron_scheduler({"redis": redis})

        assert "fired=1" in result
        svc.trigger_run.assert_awaited_once()

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_cron_skips_recently_fired(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_cron import workflow_cron_scheduler

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        row = MagicMock()
        row.id = uuid.uuid4()
        row.definition = {
            "nodes": [
                {"type": "trigger", "config": {"trigger_type": "cron", "cron_expression": "* * * * *"}},
            ],
        }
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[row]))
        redis = AsyncMock()
        redis.get.return_value = datetime.now(UTC).isoformat()  # just fired
        mock_redis_fn.return_value = redis

        result = await workflow_cron_scheduler({"redis": redis})

        assert "fired=0" in result

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_cron_marks_last_fire_before_committing_the_run(
        self, mock_redis_fn, mock_session_cm
    ) -> None:
        """F-34: the debounce marker must be written before the run is
        committed, so a failure downstream cannot leave a duplicate-fire
        window with nothing recording that this trigger already fired."""
        from app.workers.tasks.workflow_cron import workflow_cron_scheduler

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        wf_id = uuid.uuid4()
        row = MagicMock()
        row.id = wf_id
        row.definition = {
            "nodes": [
                {"type": "trigger", "config": {"trigger_type": "cron", "cron_expression": "* * * * *"}},
            ],
        }
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[row]))
        redis = AsyncMock()
        redis.get.return_value = None
        mock_redis_fn.return_value = redis

        manager = MagicMock()
        manager.attach_mock(redis.set, "redis_set")
        manager.attach_mock(db.commit, "db_commit")

        svc = AsyncMock()
        with patch(
            "contexts.workflow.application.workflow_service.WorkflowService",
            return_value=svc,
        ):
            result = await workflow_cron_scheduler({"redis": redis})

        assert "fired=1" in result
        redis.set.assert_awaited_once()
        assert redis.set.await_args.args[0] == f"wf:cron:{wf_id}:last_fire"
        call_names = [c[0] for c in manager.mock_calls if c[0] in ("redis_set", "db_commit")]
        assert call_names.index("redis_set") < call_names.index("db_commit")

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_cron_does_not_refire_when_dispatch_fails(self, mock_redis_fn, mock_session_cm) -> None:
        """F-34: a post-mark dispatch failure must not cause a second fire on
        the very next pass — the marker was already written."""
        from app.workers.tasks.workflow_cron import workflow_cron_scheduler

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        wf_id = uuid.uuid4()
        row = MagicMock()
        row.id = wf_id
        row.definition = {
            "nodes": [
                {"type": "trigger", "config": {"trigger_type": "cron", "cron_expression": "* * * * *"}},
            ],
        }
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[row]))
        redis = AsyncMock()
        redis.get.return_value = None
        mock_redis_fn.return_value = redis

        svc = AsyncMock()
        svc.dispatch_pending.side_effect = RuntimeError("dispatch boom")
        with (
            patch(
                "contexts.workflow.application.workflow_service.WorkflowService",
                return_value=svc,
            ),
            pytest.raises(RuntimeError),
        ):
            # The lone eligible workflow errored, so the all-eligible-failed
            # guard (workflow_cron.py) re-raises to arq — unrelated to F-34,
            # this dossier does not change that behavior.
            await workflow_cron_scheduler({"redis": redis})

        redis.set.assert_awaited_once()
        marked_at = redis.set.await_args.args[1]

        # A second, immediate pass must see the marker and not re-fire.
        redis.get.return_value = marked_at
        second = await workflow_cron_scheduler({"redis": redis})

        assert "fired=0" in second

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_cron_skips_non_cron_triggers(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_cron import workflow_cron_scheduler

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)

        row = MagicMock()
        row.id = uuid.uuid4()
        row.definition = {
            "nodes": [
                {"type": "trigger", "config": {"trigger_type": "manual"}},
            ],
        }
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[row]))
        redis = AsyncMock()
        mock_redis_fn.return_value = redis

        result = await workflow_cron_scheduler({"redis": redis})

        assert "fired=0" in result

    @patch("shared_kernel.db.session.async_session")
    @patch("shared_kernel.auth.clients.get_redis")
    async def test_cron_no_workflows(self, mock_redis_fn, mock_session_cm) -> None:
        from app.workers.tasks.workflow_cron import workflow_cron_scheduler

        db = AsyncMock()
        mock_session_cm.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_cm.return_value.__aexit__ = AsyncMock(return_value=False)
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[]))
        redis = AsyncMock()
        mock_redis_fn.return_value = redis

        result = await workflow_cron_scheduler({"redis": redis})

        assert "fired=0" in result
