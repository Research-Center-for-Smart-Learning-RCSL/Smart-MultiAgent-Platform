"""Unit tests for RunEngine core logic — on_error strategies and parallel fan-out.

Tests cover the pure-logic paths inside _apply_on_error and _advance_from so
that parallel branching, join routing, and every on_error strategy are verified
without requiring a live database or Redis instance.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.workflow.application.run_engine import RunEngine
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    OnErrorConfig,
    OnErrorStrategy,
    RunContext,
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


# ---------------------------------------------------------------------------
# _apply_on_error — CONTINUE
# ---------------------------------------------------------------------------


async def test_on_error_continue_converts_to_succeeded() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.CONTINUE)

    result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.state == StepState.SUCCEEDED
    assert result.port == "default"


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


async def test_on_error_retry_schedules_when_budget_remains() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=3, retry_backoff_ms=100)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # no prior retries

    with patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis):
        result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.state == StepState.RUNNING
    assert result.park is True
    assert len(engine._pending_enqueues) == 1
    task_name, run_id_str, node_id, delay_ms = engine._pending_enqueues[0]
    assert task_name == "retry_workflow_node"
    assert run_id_str == str(ctx.run_id)
    assert node_id == "n1"
    assert delay_ms == 100  # backoff_ms * 1


async def test_on_error_retry_backoff_grows_with_attempt() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=5, retry_backoff_ms=200)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = b"2"  # already retried twice

    with patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis):
        result = await engine._apply_on_error(ctx, node, _failed_outcome(), uuid.uuid4())

    assert result.park is True
    _, _, _, delay_ms = engine._pending_enqueues[0]
    assert delay_ms == 200 * 3  # retry_backoff_ms * new_count (3)


async def test_on_error_retry_exhausted_returns_failed() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=2)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = b"2"  # already at max

    with patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis):
        original = _failed_outcome()
        result = await engine._apply_on_error(ctx, node, original, uuid.uuid4())

    assert result is original
    assert engine._pending_enqueues == []


async def test_on_error_retry_exhausted_emits_warning() -> None:
    engine = _engine()
    ctx = _make_ctx()
    node = _make_node(strategy=OnErrorStrategy.RETRY, retry_max=1)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = b"1"  # at the limit

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

    engine._execute_node.assert_awaited_once_with(ctx, "n2")
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

    engine._execute_node.assert_awaited_once_with(ctx, "n2")


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


async def test_advance_from_three_branches_sets_active_branches() -> None:
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

    assert ctx.active_branches == 3
    assert len(engine._pending_enqueues) == 3
