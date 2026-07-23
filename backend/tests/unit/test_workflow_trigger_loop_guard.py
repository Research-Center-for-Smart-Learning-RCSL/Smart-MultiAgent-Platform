"""Trigger-level loop guard for the a2a_event self-amplification defect (F-4).

Part 1 (this file's linter tests): a workflow whose ``a2a_event`` trigger targets
the same agent one of its own nodes sends a matching envelope to forms a closed
causal cycle that nothing else breaks. Lint rule 17 rejects that shape at save
time. It compares the *emitted* message type against the trigger's
``event_types`` so a disjoint pair (trigger on ``notify``, node emits ``call``)
stays legal.

Part 3 (the run_triggered_workflow budget tests): the shared trigger-start path
enforces a per-workflow rolling-window ceiling so a missed provenance path still
cannot spend without bound. See docs/tasks/2026-07-22-a2a-event-trigger-loop-guard.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from contexts.workflow.application.linter import validate_definition

_A = "11111111-1111-1111-1111-111111111111"
_B = "22222222-2222-2222-2222-222222222222"


def _self_trigger_defn(
    *,
    trigger_agent: str,
    event_types: list[str],
    invoke_agent: str,
) -> dict[str, Any]:
    """Minimal definition that passes rules 1-16: trigger -> agent_invocation ->
    end, with both agent_invocation ports connected."""
    return {
        "schema_version": "1.0",
        "name": "self-trigger",
        "entry_node_id": "t1",
        "nodes": [
            {
                "id": "t1",
                "type": "trigger",
                "config": {
                    "trigger_type": "a2a_event",
                    "agent_id": trigger_agent,
                    "event_types": event_types,
                },
            },
            {
                "id": "a1",
                "type": "agent_invocation",
                "config": {"agent_id": invoke_agent, "input_template": "hi"},
            },
            {"id": "e1", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "ed1", "from": "t1", "to": "a1", "from_port": "default"},
            {"id": "ed2", "from": "a1", "to": "e1", "from_port": "success"},
            {"id": "ed3", "from": "a1", "to": "e1", "from_port": "failure"},
        ],
    }


def _instruct_trigger_defn(
    *,
    trigger_agent: str,
    event_types: list[str],
    issuer_agent: str,
    target_agent: str,
) -> dict[str, Any]:
    """Instruct-edged variant: trigger -> instruct -> end."""
    return {
        "schema_version": "1.0",
        "name": "instruct-self-trigger",
        "entry_node_id": "t1",
        "nodes": [
            {
                "id": "t1",
                "type": "trigger",
                "config": {
                    "trigger_type": "a2a_event",
                    "agent_id": trigger_agent,
                    "event_types": event_types,
                },
            },
            {
                "id": "i1",
                "type": "instruct",
                "config": {
                    "issuer_agent_id": issuer_agent,
                    "target_agent_id": target_agent,
                    "instruction_template": "do x",
                },
            },
            {"id": "e1", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "ed1", "from": "t1", "to": "i1", "from_port": "default"},
            {"id": "ed2", "from": "i1", "to": "e1", "from_port": "success"},
            {"id": "ed3", "from": "i1", "to": "e1", "from_port": "failure"},
        ],
    }


class TestRule17SelfTrigger:
    def test_a2a_event_trigger_may_not_target_an_agent_the_workflow_invokes(self) -> None:
        # T-1: trigger on A + agent_invocation on A emitting `call`, and the
        # trigger listens for `call` — a closed self-amplifying cycle.
        defn = _self_trigger_defn(trigger_agent=_A, event_types=["call"], invoke_agent=_A)
        result = validate_definition(defn, valid_agent_ids=frozenset({_A}))
        assert result.valid is False
        assert any(e.rule == 17 for e in result.errors)

    def test_lint_allows_a_trigger_whose_event_types_exclude_the_emitted_type(self) -> None:
        # T-2: the false-positive floor. agent_invocation emits `call`, but the
        # trigger only listens for `notify` — no cycle, must stay legal.
        defn = _self_trigger_defn(trigger_agent=_A, event_types=["notify"], invoke_agent=_A)
        result = validate_definition(defn, valid_agent_ids=frozenset({_A}))
        assert not any(e.rule == 17 for e in result.errors)
        assert result.valid is True

    def test_lint_allows_invocation_of_a_different_agent(self) -> None:
        # A trigger on A whose node invokes B is not a self-cycle.
        defn = _self_trigger_defn(trigger_agent=_A, event_types=["call"], invoke_agent=_B)
        result = validate_definition(defn, valid_agent_ids=frozenset({_A, _B}))
        assert not any(e.rule == 17 for e in result.errors)
        assert result.valid is True

    def test_lint_rejects_the_instruct_edged_cycle(self) -> None:
        # T-3: the sibling that goes live when F-9 (a2a-scope-context-wiring)
        # lands. instruct emits `instruct` to B; trigger on B listens for it.
        defn = _instruct_trigger_defn(
            trigger_agent=_B, event_types=["instruct"], issuer_agent=_A, target_agent=_B
        )
        result = validate_definition(defn, valid_agent_ids=frozenset({_A, _B}))
        assert result.valid is False
        assert any(e.rule == 17 for e in result.errors)

    def test_lint_allows_instruct_when_event_types_exclude_instruct(self) -> None:
        # Instruct emits `instruct`; a trigger on B listening only for `call`
        # does not form a cycle.
        defn = _instruct_trigger_defn(
            trigger_agent=_B, event_types=["call"], issuer_agent=_A, target_agent=_B
        )
        result = validate_definition(defn, valid_agent_ids=frozenset({_A, _B}))
        assert not any(e.rule == 17 for e in result.errors)
        assert result.valid is True


# --------------------------------------------------------------------------- #
# Part 3: per-workflow trigger budget on the shared run_triggered_workflow path
# --------------------------------------------------------------------------- #


class _FakeRedis:
    """Minimal sorted-set fake for the sliding-window rate limiter."""

    def __init__(self) -> None:
        self._z: dict[str, list[tuple[str, float]]] = {}

    async def zremrangebyscore(self, key: str, mn: float, mx: float) -> None:
        self._z[key] = [(m, s) for (m, s) in self._z.get(key, []) if not (mn <= s <= mx)]

    async def zcard(self, key: str) -> int:
        return len(self._z.get(key, []))

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._z.setdefault(key, []).extend(mapping.items())

    async def expire(self, key: str, ttl: int) -> None:
        return None


def _settings(limit: int, window_s: int = 60) -> SimpleNamespace:
    return SimpleNamespace(
        limits=SimpleNamespace(
            workflow_trigger_per_window=limit,
            workflow_trigger_window_seconds=window_s,
        )
    )


class TestTriggerBudget:
    async def test_trigger_budget_blocks_the_run_past_the_window_cap(self) -> None:
        # T-6/AC-5: N tokens, then throttle. The N+1th call must NOT start a run,
        # must return the throttle sentinel (never "error"), and must audit.
        from app.workers.tasks.workflow_signals import (
            TRIGGER_THROTTLED_SENTINEL,
            run_triggered_workflow,
        )

        n = 3
        redis = _FakeRedis()
        wf_id = str(uuid.uuid4())
        svc = AsyncMock()
        svc.trigger_run.return_value = uuid.uuid4()
        audits: list[Any] = []

        async def _capture_emit(_db: Any, event: Any) -> None:
            audits.append(event)

        results: list[str] = []
        with (
            patch("app.config.settings.get_settings", return_value=_settings(n)),
            patch("shared_kernel.auth.clients.get_redis", return_value=redis),
            patch("contexts.workflow.application.workflow_service.WorkflowService", return_value=svc),
            patch("shared_kernel.db.session.async_session") as mock_sc,
            patch("shared_kernel.audit.emit", new=_capture_emit),
        ):
            db = AsyncMock()
            mock_sc.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_sc.return_value.__aexit__ = AsyncMock(return_value=False)
            for _ in range(n + 1):
                results.append(await run_triggered_workflow({"redis": AsyncMock()}, wf_id, {}))

        # Exactly N runs started; the last was throttled, not errored.
        assert svc.trigger_run.await_count == n
        assert results[-1] == TRIGGER_THROTTLED_SENTINEL
        assert results[-1] != "error"
        # The throttle was recorded in the audit trail.
        assert any(a.action == "workflow.trigger_throttled" for a in audits)

    async def test_trigger_budget_is_scoped_per_workflow(self) -> None:
        # T-7/AC-5: exhausting workflow 1's budget must not silence workflow 2.
        # This is the test that fails if the breaker is ever made a shared counter.
        from app.workers.tasks.workflow_signals import run_triggered_workflow

        redis = _FakeRedis()
        wf1, wf2 = str(uuid.uuid4()), str(uuid.uuid4())
        svc = AsyncMock()
        run_id = uuid.uuid4()
        svc.trigger_run.return_value = run_id

        async def _noop_emit(_db: Any, _event: Any) -> None:
            return None

        with (
            patch("app.config.settings.get_settings", return_value=_settings(2)),
            patch("shared_kernel.auth.clients.get_redis", return_value=redis),
            patch("contexts.workflow.application.workflow_service.WorkflowService", return_value=svc),
            patch("shared_kernel.db.session.async_session") as mock_sc,
            patch("shared_kernel.audit.emit", new=_noop_emit),
        ):
            db = AsyncMock()
            mock_sc.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_sc.return_value.__aexit__ = AsyncMock(return_value=False)
            # Exhaust wf1 (2 allowed + 1 throttled).
            for _ in range(3):
                await run_triggered_workflow({"redis": AsyncMock()}, wf1, {})
            # wf2 is a different key — still starts.
            result = await run_triggered_workflow({"redis": AsyncMock()}, wf2, {})

        assert result == str(run_id)

    async def test_trigger_budget_disabled_when_limit_non_positive(self) -> None:
        # The emergency lever: limit=0 disables the breaker without a deploy.
        from app.workers.tasks.workflow_signals import run_triggered_workflow

        redis = _FakeRedis()
        wf_id = str(uuid.uuid4())
        svc = AsyncMock()
        svc.trigger_run.return_value = uuid.uuid4()

        with (
            patch("app.config.settings.get_settings", return_value=_settings(0)),
            patch("shared_kernel.auth.clients.get_redis", return_value=redis),
            patch("contexts.workflow.application.workflow_service.WorkflowService", return_value=svc),
            patch("shared_kernel.db.session.async_session") as mock_sc,
        ):
            db = AsyncMock()
            mock_sc.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_sc.return_value.__aexit__ = AsyncMock(return_value=False)
            for _ in range(50):
                await run_triggered_workflow({"redis": AsyncMock()}, wf_id, {})

        assert svc.trigger_run.await_count == 50
