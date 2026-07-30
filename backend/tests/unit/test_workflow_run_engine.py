"""Unit tests for RunEngine core logic — on_error strategies and parallel fan-out.

Tests cover the pure-logic paths inside _apply_on_error and _advance_from so
that parallel branching, join routing, and every on_error strategy are verified
without requiring a live database or Redis instance.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.workflow.application.run_engine import RunEngine
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    OnErrorConfig,
    OnErrorStrategy,
    RunContext,
    RunState,
    StepOutcome,
    StepState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(definition: dict | None = None) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        workflow_def=definition or {"nodes": [], "edges": []},
        variables={},
    )


def _make_node(
    strategy: OnErrorStrategy = OnErrorStrategy.FAIL,
    *,
    retry_max: int = 0,
    retry_backoff_ms: int = 100,
    fallback_node_id: str | None = None,
    node_type: NodeType = NodeType.INSTRUCT,
) -> NodeSpec:
    return NodeSpec(
        id="n1",
        type=node_type,
        config={},
        on_error=OnErrorConfig(
            strategy=strategy,
            retry_max=retry_max,
            retry_backoff_ms=retry_backoff_ms,
            fallback_node_id=fallback_node_id,
        ),
    )


def _failed_outcome() -> StepOutcome:
    return StepOutcome(state=StepState.FAILED, error="boom")


def _engine() -> RunEngine:
    return RunEngine(db=MagicMock())


def _def_with_edges(edges: list[dict]) -> dict:
    return {"nodes": [], "edges": edges}


def _run(state: RunState) -> SimpleNamespace:
    return SimpleNamespace(state=state)


# ---------------------------------------------------------------------------
# Run terminality
# ---------------------------------------------------------------------------


async def test_execute_node_refuses_a_terminal_run() -> None:
    engine = _engine()
    ctx = _make_ctx(
        {
            "nodes": [
                {"id": "n1", "type": "set_variable", "config": {"key": "x", "value": 1}},
            ],
            "edges": [],
        }
    )
    engine._runs.get = AsyncMock(return_value=_run(RunState.FAILED))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock()
    executor = AsyncMock(return_value=StepOutcome(state=StepState.SUCCEEDED))

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "n1")

    assert ctx.cancelled is True
    engine._recorder.insert_step.assert_not_awaited()
    executor.assert_not_awaited()


async def test_execute_node_observes_sibling_failure_at_next_node_boundary() -> None:
    engine = _engine()
    ctx = _make_ctx(
        {
            "nodes": [
                {"id": "a", "type": "set_variable", "config": {"key": "a", "value": 1}},
                {"id": "b", "type": "set_variable", "config": {"key": "b", "value": 2}},
            ],
            "edges": [{"id": "e1", "from": "a", "to": "b", "from_port": "default"}],
        }
    )
    engine._runs.get = AsyncMock(side_effect=[_run(RunState.RUNNING), _run(RunState.FAILED)])
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock()
    executor = AsyncMock(return_value=StepOutcome(state=StepState.SUCCEEDED))

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "a")

    assert engine._recorder.insert_step.await_count == 1
    assert engine._recorder.insert_step.await_args.kwargs["node_id"] == "a"
    assert executor.await_count == 1


async def test_execute_node_refuses_a_cancelled_run() -> None:
    engine = _engine()
    ctx = _make_ctx({"nodes": [{"id": "n1", "type": "set_variable", "config": {}}], "edges": []})
    engine._runs.get = AsyncMock(return_value=_run(RunState.CANCELLED))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock()
    executor = AsyncMock(return_value=StepOutcome(state=StepState.SUCCEEDED))

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "n1")

    engine._recorder.insert_step.assert_not_awaited()


async def test_execute_node_runs_while_the_run_is_live() -> None:
    engine = _engine()
    ctx = _make_ctx({"nodes": [{"id": "n1", "type": "set_variable", "config": {}}], "edges": []})
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock()
    executor = AsyncMock(return_value=StepOutcome(state=StepState.SUCCEEDED))

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "n1")

    engine._recorder.insert_step.assert_awaited_once()
    executor.assert_awaited_once()


async def test_terminal_guard_reads_once_per_node() -> None:
    engine = _engine()
    ctx = _make_ctx(
        {
            "nodes": [
                {"id": "a", "type": "set_variable", "config": {}},
                {"id": "b", "type": "set_variable", "config": {}},
                {"id": "c", "type": "set_variable", "config": {}},
            ],
            "edges": [
                {"id": "e1", "from": "a", "to": "b", "from_port": "default"},
                {"id": "e2", "from": "b", "to": "c", "from_port": "default"},
            ],
        }
    )
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock()
    executor = AsyncMock(return_value=StepOutcome(state=StepState.SUCCEEDED))

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "a")

    assert engine._runs.get.await_count == 3


async def test_terminal_run_during_execution_does_not_persist_variables_or_advance() -> None:
    engine = _engine()
    ctx = _make_ctx(
        {
            "nodes": [
                {"id": "a", "type": "set_variable", "config": {}},
                {"id": "b", "type": "set_variable", "config": {}},
            ],
            "edges": [{"id": "e1", "from": "a", "to": "b", "from_port": "default"}],
        }
    )
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock(return_value=False)
    executor = AsyncMock(return_value=StepOutcome(state=StepState.SUCCEEDED))

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "a")

    engine._recorder.insert_step.assert_awaited_once()
    executor.assert_awaited_once()


async def test_execute_node_does_not_invoke_executor_when_atomic_claim_loses_race() -> None:
    engine = _engine()
    ctx = _make_ctx({"nodes": [{"id": "n1", "type": "set_variable", "config": {}}], "edges": []})
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._recorder.insert_step = AsyncMock(return_value=None)
    executor = AsyncMock(return_value=StepOutcome(state=StepState.SUCCEEDED))

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "n1")

    executor.assert_not_awaited()
    assert ctx.cancelled is True


async def test_resume_losing_transition_does_not_seal_or_advance(monkeypatch) -> None:
    engine = _engine()
    run = SimpleNamespace(
        state=RunState.WAITING,
        workflow_id=uuid.uuid4(),
        variables={},
        context={},
        trigger_type="manual",
    )
    engine._runs.get = AsyncMock(return_value=run)
    engine._runs.get_project_id = AsyncMock(return_value=uuid.uuid4())
    engine._runs.update_state = AsyncMock(return_value=False)
    engine._db.execute = AsyncMock()
    engine._advance_from = AsyncMock()  # type: ignore[method-assign]

    workflow = SimpleNamespace(definition={"nodes": [], "edges": []})
    with patch(
        "contexts.workflow.infrastructure.repositories.WorkflowRepository.get",
        new=AsyncMock(return_value=workflow),
    ):
        resumed = await engine.resume_at_port(uuid.uuid4(), "gate", "success")

    assert resumed is False
    engine._db.execute.assert_not_awaited()
    engine._advance_from.assert_not_awaited()


async def test_terminal_loser_emits_no_cancellation_or_events(monkeypatch) -> None:
    engine = _engine()
    run_id = uuid.uuid4()
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._runs.update_state = AsyncMock(return_value=False)
    engine._steps.cancel_pending_for_run = AsyncMock()

    with (
        patch("contexts.workflow.application.run_engine.audit.emit", new=AsyncMock()) as emit_audit,
        patch("contexts.workflow.application.run_engine.Publisher") as publisher,
    ):
        transitioned = await engine.force_fail(run_id, reason="race")

    assert transitioned is False
    engine._steps.cancel_pending_for_run.assert_not_awaited()
    emit_audit.assert_not_awaited()
    publisher.return_value.emit.assert_not_called()
    assert engine._pending_call_cancellations == set()


async def test_end_node_cancels_pending_sibling_steps(monkeypatch) -> None:
    engine = _engine()
    ctx = _make_ctx({"nodes": [{"id": "end", "type": "end", "config": {"status": "success"}}], "edges": []})
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock()
    engine._runs.update_state = AsyncMock()
    engine._runs.mark_a2a_cancellation_pending = AsyncMock()
    engine._steps.cancel_pending_for_run = AsyncMock()
    cancel_calls = AsyncMock()
    monkeypatch.setattr(engine, "_cancel_live_agent_calls", cancel_calls)

    with (
        patch("contexts.workflow.application.run_engine.audit.emit", new=AsyncMock()),
        patch("contexts.workflow.application.run_engine.Publisher") as publisher,
    ):
        publisher.return_value.emit = AsyncMock()
        await engine._execute_node(ctx, "end")

    engine._steps.cancel_pending_for_run.assert_awaited_once_with(ctx.run_id)
    cancel_calls.assert_not_awaited()

    await engine.dispatch_enqueues()

    cancel_calls.assert_awaited_once_with(ctx.run_id)


async def test_mark_run_failed_isolated_cancels_pending_steps(monkeypatch) -> None:
    engine = _engine()
    session = MagicMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    transaction_cm = MagicMock()
    transaction_cm.__aenter__ = AsyncMock(return_value=None)
    transaction_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = transaction_cm
    runs = MagicMock(update_state=AsyncMock(), mark_a2a_cancellation_pending=AsyncMock())
    steps = MagicMock(cancel_pending_for_run=AsyncMock())
    cancel_calls = AsyncMock()
    monkeypatch.setattr(engine, "_cancel_live_agent_calls", cancel_calls)

    with (
        patch("shared_kernel.db.session.async_session", return_value=session_cm),
        patch("contexts.workflow.application.run_engine.WorkflowRunRepository", return_value=runs),
        patch("contexts.workflow.application.run_engine.WorkflowStepRepository", return_value=steps),
    ):
        await engine._mark_run_failed_isolated(ctx_run_id := uuid.uuid4())

    runs.update_state.assert_awaited_once()
    runs.mark_a2a_cancellation_pending.assert_awaited_once_with(ctx_run_id)
    steps.cancel_pending_for_run.assert_awaited_once_with(ctx_run_id)
    cancel_calls.assert_awaited_once_with(ctx_run_id)


# ---------------------------------------------------------------------------
# _apply_on_error — CONTINUE
# ---------------------------------------------------------------------------


async def test_on_error_continue_converts_to_succeeded() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.CONTINUE, node_type=NodeType.SET_VARIABLE)

    result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.state == StepState.SUCCEEDED
    assert result.port == "default"


# `continue` resolved to "default" for every node type, but the four multi-port types
# cannot emit "default" (linter._ALLOWED_PORTS; a "default" edge from one of them is a
# rule-3 save error). _advance_from matched nothing, the branch dead-ended, and the run
# sat RUNNING until the idle watchdog killed it ~30 min later.
@pytest.mark.parametrize(
    ("node_type", "expected_port"),
    [
        (NodeType.AGENT_INVOCATION, "success"),
        (NodeType.INSTRUCT, "success"),
        (NodeType.SUBAGENT_SPAWN, "success"),
        (NodeType.APPROVAL_GATE, "rejected"),
        (NodeType.SET_VARIABLE, "default"),
        (NodeType.WAIT_FOR_EVENT, "default"),
        (NodeType.JOIN, "default"),
        (NodeType.PARALLEL, "default"),
    ],
)
async def test_on_error_continue_resolves_an_emittable_port(
    node_type: NodeType,
    expected_port: str,
) -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.CONTINUE, node_type=node_type)

    result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.port == expected_port


async def test_on_error_continue_never_manufactures_an_approval() -> None:
    # An error inside the approval machinery must fail closed. Routing `continue` to
    # "approved" would let a crashed gate authorize whatever it guards.
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.CONTINUE, node_type=NodeType.APPROVAL_GATE)

    result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.port != "approved"


async def test_on_error_continue_uses_conditions_declared_default_port() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = NodeSpec(
        id="n1",
        type=NodeType.CONDITION,
        config={"default_port": "fallback"},
        on_error=OnErrorConfig(strategy=OnErrorStrategy.CONTINUE),
    )

    result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.port == "fallback"


def _continue_harness(engine: RunEngine) -> AsyncMock:
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock(return_value=True)
    engine._fail_run = AsyncMock()  # type: ignore[method-assign]
    # The wired-port case advances into the end node; stub its terminal write so the
    # test exercises real _advance_from routing without a live DB.
    engine._finalize_run = AsyncMock()  # type: ignore[method-assign]
    return AsyncMock(return_value=StepOutcome(state=StepState.FAILED, error="boom"))


def _continue_def(from_port: str) -> dict:
    return {
        "nodes": [
            {"id": "s1", "type": "subagent_spawn", "config": {"on_error": {"strategy": "continue"}}},
            {"id": "fin", "type": "end", "config": {}},
        ],
        "edges": [{"id": "x1", "from": "s1", "to": "fin", "from_port": from_port}],
    }


async def test_continue_with_no_matching_edge_fails_the_run_instead_of_stalling() -> None:
    # Only "failure" is wired, but `continue` resolves to "success". Before the guard
    # the branch just stopped and the run sat RUNNING until the idle watchdog.
    engine = _engine()
    ctx = _make_ctx(_continue_def("failure"))
    executor = _continue_harness(engine)

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "s1")

    engine._fail_run.assert_awaited_once()
    reason = engine._fail_run.await_args[0][1]
    assert "success" in reason
    # The original error must survive into the reason, or the failure is undiagnosable.
    assert "boom" in reason


async def test_continue_advances_normally_when_the_resolved_port_is_wired() -> None:
    engine = _engine()
    ctx = _make_ctx(_continue_def("success"))
    executor = _continue_harness(engine)

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "s1")

    engine._fail_run.assert_not_awaited()


async def test_continue_on_an_edgeless_node_does_not_fail_the_run() -> None:
    # linter rule 5 permits a wait_for_event with NO outgoing edges as a warning
    # ("permanent listener"), so that branch is meant to stop. Failing the run there
    # would cancel healthy parallel siblings.
    engine = _engine()
    ctx = _make_ctx(
        {
            "nodes": [
                {
                    "id": "listener",
                    "type": "wait_for_event",
                    "config": {"on_error": {"strategy": "continue"}},
                }
            ],
            "edges": [],
        }
    )
    executor = _continue_harness(engine)

    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "listener")

    engine._fail_run.assert_not_awaited()


# ---------------------------------------------------------------------------
# Dry run — same port mismatch, reached without on_error
# ---------------------------------------------------------------------------


async def test_dry_run_mock_advances_on_a_port_the_node_can_emit() -> None:
    # The dry-run mock hardcoded port="default", which none of the mocked (multi-port)
    # types can emit, so every dry run dead-ended at its first agent_invocation.
    engine = _engine()
    ctx = _make_ctx(
        {
            "nodes": [
                {"id": "a1", "type": "agent_invocation", "config": {}},
                {"id": "fin", "type": "end", "config": {}},
            ],
            "edges": [{"id": "x1", "from": "a1", "to": "fin", "from_port": "success"}],
        }
    )
    ctx.is_dry_run = True
    engine._runs.get = AsyncMock(return_value=_run(RunState.RUNNING))
    engine._recorder.insert_step = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    engine._recorder.emit_step_started = AsyncMock()
    engine._recorder.update_step = AsyncMock()
    engine._recorder.emit_step_event = AsyncMock()
    engine._runs.update_variables = AsyncMock(return_value=True)
    engine._advance_from = AsyncMock(return_value=True)  # type: ignore[method-assign]

    executor = AsyncMock()
    with patch("contexts.workflow.application.run_engine.get_executor", return_value=executor):
        await engine._execute_node(ctx, "a1")

    executor.assert_not_awaited()  # mocked, not really invoked
    assert engine._advance_from.await_args.kwargs["port"] == "success"


# ---------------------------------------------------------------------------
# resume_at_port — dead-end onto an unwired port
# ---------------------------------------------------------------------------


def _resume_engine(edges: list[dict]):
    engine = _engine()
    engine._runs.get = AsyncMock(
        return_value=SimpleNamespace(
            state=RunState.WAITING,
            workflow_id=uuid.uuid4(),
            variables={},
            context={},
            trigger_type="manual",
        )
    )
    engine._runs.update_state = AsyncMock(return_value=True)
    engine._runs.get_project_id = AsyncMock(return_value=uuid.uuid4())
    engine._db.execute = AsyncMock()
    engine._fail_run = AsyncMock()  # type: ignore[method-assign]
    engine._advance_from = AsyncMock(return_value=False)  # type: ignore[method-assign]
    definition = {
        "nodes": [
            {"id": "w1", "type": "wait_for_event", "config": {}},
            {"id": "fin", "type": "end", "config": {}},
        ],
        "edges": edges,
    }
    return engine, SimpleNamespace(definition=definition)


async def test_resume_onto_an_unwired_port_fails_instead_of_stalling() -> None:
    # W3 (wait_for_event with no 'timeout' edge) is advisory, so a resolver can resume
    # onto a port nothing is wired to. Previously the branch just stopped.
    engine, workflow = _resume_engine([{"id": "x1", "from": "w1", "to": "fin", "from_port": "default"}])

    with patch(
        "contexts.workflow.infrastructure.repositories.WorkflowRepository.get",
        new=AsyncMock(return_value=workflow),
    ):
        resumed = await engine.resume_at_port(uuid.uuid4(), "w1", "timeout")

    # True: the resume happened, so the caller must consume its single-shot claim.
    # Returning False would send it into a restore-and-retry loop that cannot succeed.
    assert resumed is True
    engine._fail_run.assert_awaited_once()
    assert "timeout" in engine._fail_run.await_args[0][1]


async def test_resume_onto_an_edgeless_node_does_not_fail_the_run() -> None:
    engine, workflow = _resume_engine([])

    with patch(
        "contexts.workflow.infrastructure.repositories.WorkflowRepository.get",
        new=AsyncMock(return_value=workflow),
    ):
        resumed = await engine.resume_at_port(uuid.uuid4(), "w1", "default")

    assert resumed is True
    engine._fail_run.assert_not_awaited()


async def test_on_error_continue_preserves_output() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.CONTINUE)
    original = StepOutcome(state=StepState.FAILED, output={"key": "val"}, error="e")

    result = await engine._apply_on_error(ctx, node, original, uuid.uuid4())

    assert result.output == {"key": "val"}


# ---------------------------------------------------------------------------
# _apply_on_error — FAIL (default)
# ---------------------------------------------------------------------------


async def test_on_error_fail_returns_original_outcome() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.FAIL)
    original = _failed_outcome()

    result = await engine._apply_on_error(ctx, node, original, uuid.uuid4())

    assert result is original


# ---------------------------------------------------------------------------
# _apply_on_error — RETRY
# ---------------------------------------------------------------------------


def _mock_redis_pipeline(incr_result: int = 1) -> MagicMock:
    """Return a mock Redis whose pipeline().execute() returns [incr_result, True]."""
    mock_redis = MagicMock()
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=[incr_result, True])
    mock_redis.pipeline.return_value = mock_pipe
    return mock_redis


async def test_on_error_retry_schedules_when_budget_remains() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=3, retry_backoff_ms=100)

    mock_redis = _mock_redis_pipeline(incr_result=1)

    with patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis):
        result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.state == StepState.RUNNING
    assert result.park is True
    assert len(engine._pending_enqueues) == 1
    task_name, run_id_str, node_id, delay_ms, from_edge = engine._pending_enqueues[0]
    assert task_name == "retry_workflow_node"
    assert run_id_str == str(ctx.run_id)
    assert node_id == "n1"
    assert delay_ms == 100  # backoff_ms * 1
    assert from_edge is None  # retry tasks carry no spawning edge


async def test_on_error_retry_backoff_grows_with_attempt() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=5, retry_backoff_ms=200)

    mock_redis = _mock_redis_pipeline(incr_result=3)  # third attempt

    with patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis):
        result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.park is True
    _, _, _, delay_ms, _from_edge = engine._pending_enqueues[0]
    assert delay_ms == 200 * 3  # retry_backoff_ms * new_count (3)


async def test_on_error_retry_exhausted_returns_failed() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=2)

    mock_redis = _mock_redis_pipeline(incr_result=3)  # exceeds max

    with patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis):
        original = _failed_outcome()
        result = await engine._apply_on_error(ctx, node, original, uuid.uuid4())

    assert result is original
    assert engine._pending_enqueues == []


async def test_on_error_retry_exhausted_emits_warning() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=1)

    mock_redis = _mock_redis_pipeline(incr_result=2)  # exceeds max

    with (
        patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis),
        patch("contexts.workflow.application.run_engine.logger") as mock_log,
    ):
        await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    mock_log.warning.assert_called()
    first_arg = mock_log.warning.call_args.args[0]
    assert "exhausted" in first_arg


# ---------------------------------------------------------------------------
# _apply_on_error — FALLBACK
# ---------------------------------------------------------------------------


async def test_on_error_fallback_executes_fallback_node() -> None:
    engine = _engine()
    engine._execute_node = AsyncMock()  # type: ignore[method-assign]
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.FALLBACK, fallback_node_id="fb_node")

    result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    engine._execute_node.assert_awaited_once_with(ctx, "fb_node")
    assert result.state == StepState.SUCCEEDED
    assert result.skip_edges is True
    assert result.output == {"fallback_node": "fb_node"}


async def test_on_error_fallback_no_node_id_returns_failed() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.FALLBACK, fallback_node_id=None)
    original = _failed_outcome()

    result = await engine._apply_on_error(ctx, node, original, uuid.uuid4())

    assert result is original


async def test_on_error_fallback_no_node_id_emits_warning() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.FALLBACK, fallback_node_id=None)

    with patch("contexts.workflow.application.run_engine.logger") as mock_log:
        await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    mock_log.warning.assert_called()
    first_arg = mock_log.warning.call_args.args[0]
    assert "fallback_node_id" in first_arg


# ---------------------------------------------------------------------------
# _advance_from — edge routing and parallel fan-out
# ---------------------------------------------------------------------------


async def test_advance_from_single_edge_calls_execute_node() -> None:
    engine = _engine()
    engine._execute_node = AsyncMock()  # type: ignore[method-assign]
    ctx = _make_ctx(
        _def_with_edges(
            [
                {"id": "e1", "from": "n1", "to": "n2", "from_port": "default"},
            ]
        )
    )

    await engine._advance_from(ctx, "n1")

    # ASYNC-9: the traversed edge id is threaded through so the join executor
    # can dedupe fan-in arrivals per incoming branch.
    engine._execute_node.assert_awaited_once_with(ctx, "n2", from_edge="e1")
    assert engine._pending_enqueues == []


async def test_advance_from_multiple_edges_enqueues_parallel_tasks() -> None:
    engine = _engine()
    engine._execute_node = AsyncMock()  # type: ignore[method-assign]
    ctx = _make_ctx(
        _def_with_edges(
            [
                {"id": "e1", "from": "n1", "to": "n2", "from_port": "default"},
                {"id": "e2", "from": "n1", "to": "n3", "from_port": "default"},
            ]
        )
    )

    await engine._advance_from(ctx, "n1")

    # With 2+ edges, branches are queued as Arq tasks — never called inline.
    engine._execute_node.assert_not_awaited()
    assert len(engine._pending_enqueues) == 2
    targets = {entry[2] for entry in engine._pending_enqueues}
    assert targets == {"n2", "n3"}
    for entry in engine._pending_enqueues:
        assert entry[0] == "run_workflow_step"
        assert entry[3] == 0  # no delay for parallel branches
    # ASYNC-9: each branch task carries the id of the edge that spawned it.
    assert {(entry[2], entry[4]) for entry in engine._pending_enqueues} == {
        ("n2", "e1"),
        ("n3", "e2"),
    }


async def test_advance_from_no_edges_is_noop() -> None:
    engine = _engine()
    engine._execute_node = AsyncMock()  # type: ignore[method-assign]
    ctx = _make_ctx(_def_with_edges([]))

    await engine._advance_from(ctx, "n1")

    engine._execute_node.assert_not_awaited()
    assert engine._pending_enqueues == []


async def test_advance_from_port_filters_non_matching_edges() -> None:
    engine = _engine()
    engine._execute_node = AsyncMock()  # type: ignore[method-assign]
    ctx = _make_ctx(
        _def_with_edges(
            [
                {"id": "e1", "from": "n1", "to": "n2", "from_port": "true"},
                {"id": "e2", "from": "n1", "to": "n3", "from_port": "false"},
            ]
        )
    )

    await engine._advance_from(ctx, "n1", port="true")

    engine._execute_node.assert_awaited_once_with(ctx, "n2", from_edge="e1")


async def test_advance_from_unrelated_node_edges_ignored() -> None:
    engine = _engine()
    engine._execute_node = AsyncMock()  # type: ignore[method-assign]
    ctx = _make_ctx(
        _def_with_edges(
            [
                {"id": "e1", "from": "other", "to": "n2", "from_port": "default"},
            ]
        )
    )

    await engine._advance_from(ctx, "n1")

    engine._execute_node.assert_not_awaited()
    assert engine._pending_enqueues == []


async def test_advance_from_three_branches_enqueues_every_branch() -> None:
    engine = _engine()
    engine._execute_node = AsyncMock()  # type: ignore[method-assign]
    ctx = _make_ctx(
        _def_with_edges(
            [
                {"id": "e1", "from": "n1", "to": "a", "from_port": "default"},
                {"id": "e2", "from": "n1", "to": "b", "from_port": "default"},
                {"id": "e3", "from": "n1", "to": "c", "from_port": "default"},
            ]
        )
    )

    await engine._advance_from(ctx, "n1")

    assert len(engine._pending_enqueues) == 3
