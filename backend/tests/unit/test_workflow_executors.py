"""Unit tests for workflow node executors.

Covers: condition, set_variable, end, trigger, join, instruct, agent_invocation.
Each executor follows the signature: execute(ctx, node, db) -> StepOutcome.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepState,
)


def _make_ctx(
    *,
    variables: dict | None = None,
    trigger_payload: dict | None = None,
    workflow_def: dict | None = None,
    arrived_via: str | None = None,
) -> RunContext:
    return RunContext(
        run_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        workflow_def=workflow_def or {"nodes": [], "edges": []},
        variables=variables or {},
        trigger_payload=trigger_payload or {},
        arrived_via=arrived_via,
    )


def _make_node(
    node_type: NodeType,
    config: dict | None = None,
    *,
    node_id: str = "n1",
) -> NodeSpec:
    return NodeSpec(id=node_id, type=node_type, config=config or {})


# ===========================================================================
# condition executor
# ===========================================================================


class TestConditionExecutor:
    async def test_first_matching_branch(self) -> None:
        from contexts.workflow.application.executors.condition import execute

        ctx = _make_ctx(variables={"x": 10})
        node = _make_node(
            NodeType.CONDITION,
            {
                "branches": [
                    {"when": "{{ x }} > 20", "port": "high"},
                    {"when": "{{ x }} > 5", "port": "medium"},
                ],
                "default_port": "low",
            },
        )

        result = await execute(ctx, node, AsyncMock())

        assert result.state == StepState.SUCCEEDED
        assert result.port == "medium"
        assert result.output["matched_port"] == "medium"

    async def test_no_match_falls_to_default(self) -> None:
        from contexts.workflow.application.executors.condition import execute

        ctx = _make_ctx(variables={"x": 1})
        node = _make_node(
            NodeType.CONDITION,
            {
                "branches": [{"when": "{{ x }} > 100", "port": "high"}],
                "default_port": "fallback",
            },
        )

        result = await execute(ctx, node, AsyncMock())

        assert result.port == "fallback"
        assert result.output["expression"] == "(default)"

    async def test_empty_branches_returns_default(self) -> None:
        from contexts.workflow.application.executors.condition import execute

        ctx = _make_ctx()
        node = _make_node(NodeType.CONDITION, {"branches": [], "default_port": "only"})

        result = await execute(ctx, node, AsyncMock())

        assert result.port == "only"

    async def test_broken_expression_skipped(self) -> None:
        from contexts.workflow.application.executors.condition import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.CONDITION,
            {
                "branches": [
                    {"when": "evil_syntax(((", "port": "bad"},
                    {"when": "true", "port": "good"},
                ],
            },
        )

        result = await execute(ctx, node, AsyncMock())

        assert result.port == "good"

    async def test_trigger_scope_accessible(self) -> None:
        from contexts.workflow.application.executors.condition import execute

        ctx = _make_ctx(trigger_payload={"event_type": "click"})
        node = _make_node(
            NodeType.CONDITION,
            {
                "branches": [
                    {"when": '{{ trigger.event_type }} == "click"', "port": "clicked"},
                ],
            },
        )

        result = await execute(ctx, node, AsyncMock())

        assert result.port == "clicked"


# ===========================================================================
# set_variable executor
# ===========================================================================


class TestSetVariableExecutor:
    async def test_assigns_single_variable(self) -> None:
        from contexts.workflow.application.executors.set_variable import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.SET_VARIABLE,
            {"assignments": [{"variable": "result", "expression": "1 + 2"}]},
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.output["result"] == 3
        assert ctx.variables["result"] == 3

    async def test_assigns_multiple_variables(self) -> None:
        from contexts.workflow.application.executors.set_variable import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.SET_VARIABLE,
            {
                "assignments": [
                    {"variable": "a", "expression": "10"},
                    {"variable": "b", "expression": "{{ a }} * 2"},
                ],
            },
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert ctx.variables["a"] == 10
        assert ctx.variables["b"] == 20

    async def test_bad_expression_fails(self) -> None:
        from contexts.workflow.application.executors.set_variable import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.SET_VARIABLE,
            {"assignments": [{"variable": "x", "expression": "evil_func()"}]},
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.FAILED
        assert "evil_func" in (outcome.error or "")

    async def test_empty_assignments_succeeds(self) -> None:
        from contexts.workflow.application.executors.set_variable import execute

        ctx = _make_ctx()
        node = _make_node(NodeType.SET_VARIABLE, {"assignments": []})

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.output == {}

    async def test_reads_existing_variables(self) -> None:
        from contexts.workflow.application.executors.set_variable import execute

        ctx = _make_ctx(variables={"base": 100})
        node = _make_node(
            NodeType.SET_VARIABLE,
            {"assignments": [{"variable": "doubled", "expression": "{{ base }} * 2"}]},
        )

        await execute(ctx, node, AsyncMock())

        assert ctx.variables["doubled"] == 200


# ===========================================================================
# end executor
# ===========================================================================


class TestEndExecutor:
    async def test_success_status(self) -> None:
        from contexts.workflow.application.executors.end import execute

        ctx = _make_ctx(variables={"result": 42})
        node = _make_node(
            NodeType.END,
            {"status": "success", "return_variables": ["result"]},
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.output["status"] == "success"
        assert outcome.output["return_variables"]["result"] == 42
        assert outcome.port == ""

    async def test_failure_status(self) -> None:
        from contexts.workflow.application.executors.end import execute

        ctx = _make_ctx()
        node = _make_node(NodeType.END, {"status": "failure"})

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.FAILED
        assert outcome.output["status"] == "failure"

    async def test_default_status_is_success(self) -> None:
        from contexts.workflow.application.executors.end import execute

        ctx = _make_ctx()
        node = _make_node(NodeType.END, {})

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED

    async def test_missing_return_variable_is_none(self) -> None:
        from contexts.workflow.application.executors.end import execute

        ctx = _make_ctx(variables={})
        node = _make_node(
            NodeType.END,
            {"return_variables": ["nonexistent"]},
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.output["return_variables"]["nonexistent"] is None

    async def test_no_return_variables(self) -> None:
        from contexts.workflow.application.executors.end import execute

        ctx = _make_ctx()
        node = _make_node(NodeType.END, {})

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.output["return_variables"] == {}


# ===========================================================================
# trigger executor
# ===========================================================================


class TestTriggerExecutor:
    async def test_manual_trigger_succeeds(self) -> None:
        from contexts.workflow.application.executors.trigger import execute

        ctx = _make_ctx()
        node = _make_node(NodeType.TRIGGER, {"trigger_type": "manual"})

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.output["trigger_type"] == "manual"
        assert outcome.port == "default"

    async def test_cron_trigger_valid_expression(self) -> None:
        from contexts.workflow.application.executors.trigger import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.TRIGGER,
            {"trigger_type": "cron", "cron_expression": "0 * * * *"},
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED

    async def test_cron_trigger_empty_expression_fails(self) -> None:
        from contexts.workflow.application.executors.trigger import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.TRIGGER,
            {"trigger_type": "cron", "cron_expression": ""},
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.FAILED
        assert "non-empty" in (outcome.error or "")

    async def test_cron_trigger_invalid_expression_fails(self) -> None:
        from contexts.workflow.application.executors.trigger import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.TRIGGER,
            {"trigger_type": "cron", "cron_expression": "not a cron"},
        )

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.FAILED
        assert "invalid" in (outcome.error or "")

    async def test_default_trigger_type_is_manual(self) -> None:
        from contexts.workflow.application.executors.trigger import execute

        ctx = _make_ctx()
        node = _make_node(NodeType.TRIGGER, {})

        outcome = await execute(ctx, node, AsyncMock())

        assert outcome.output["trigger_type"] == "manual"


# ===========================================================================
# join executor
# ===========================================================================


class TestJoinExecutor:
    async def _run_join(
        self,
        *,
        mode: str = "all",
        count: int = 1,
        incoming_edges: int = 3,
        lua_result: list[int],
        arrived_via: str = "e1",
    ):
        from contexts.workflow.application.executors.join import execute

        edges = [
            {"id": f"e{i}", "from": f"src{i}", "to": "join1"}
            for i in range(incoming_edges)
        ]
        ctx = _make_ctx(
            workflow_def={"nodes": [], "edges": edges},
            arrived_via=arrived_via,
        )
        node = _make_node(NodeType.JOIN, {"mode": mode, "count": count}, node_id="join1")

        mock_redis = AsyncMock()
        mock_redis.eval.return_value = lua_result

        with patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis):
            return await execute(ctx, node, AsyncMock())

    async def test_all_mode_not_yet_finalized(self) -> None:
        outcome = await self._run_join(mode="all", incoming_edges=3, lua_result=[1, 0])

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.skip_edges is True

    async def test_all_mode_finalized(self) -> None:
        outcome = await self._run_join(mode="all", incoming_edges=3, lua_result=[3, 1])

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.skip_edges is not True
        assert outcome.port == "default"

    async def test_any_mode_first_arrival_fires(self) -> None:
        outcome = await self._run_join(mode="any", incoming_edges=3, lua_result=[1, 1])

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.port == "default"

    async def test_count_mode_threshold(self) -> None:
        outcome = await self._run_join(mode="count", count=2, incoming_edges=4, lua_result=[2, 1])

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.port == "default"
        assert outcome.output["mode"] == "count"

    async def test_count_mode_below_threshold(self) -> None:
        outcome = await self._run_join(mode="count", count=2, incoming_edges=4, lua_result=[1, 0])

        assert outcome.skip_edges is True


# ===========================================================================
# instruct executor
# ===========================================================================


class TestInstructExecutor:
    @patch("contexts.workflow.application.executors.instruct.interpolate", return_value="Do the thing")
    async def test_fire_and_forget(self, _interp) -> None:
        from contexts.workflow.application.executors.instruct import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.INSTRUCT,
            {
                "issuer_agent_id": str(uuid.uuid4()),
                "target_agent_id": str(uuid.uuid4()),
                "instruction_template": "Do {{ task }}",
                "wait_for_completion": False,
                "output_variable": "result_id",
            },
        )

        instruction = MagicMock()
        instruction.id = uuid.uuid4()
        facade_mock = AsyncMock()
        facade_mock.issue_instruct.return_value = instruction

        with patch(
            "contexts.orchestration.interfaces.facade.OrchestrationFacade",
            return_value=facade_mock,
        ):
            outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.port == "success"
        assert ctx.variables["result_id"] == str(instruction.id)

    @patch("contexts.workflow.application.executors.instruct.interpolate", return_value="Do it")
    async def test_wait_for_completion_parks(self, _interp) -> None:
        from contexts.workflow.application.executors.instruct import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.INSTRUCT,
            {
                "issuer_agent_id": str(uuid.uuid4()),
                "target_agent_id": str(uuid.uuid4()),
                "instruction_template": "t",
                "wait_for_completion": True,
            },
        )

        instruction = MagicMock()
        instruction.id = uuid.uuid4()
        facade_mock = AsyncMock()
        facade_mock.issue_instruct.return_value = instruction
        mock_redis = AsyncMock()

        with (
            patch(
                "contexts.orchestration.interfaces.facade.OrchestrationFacade",
                return_value=facade_mock,
            ),
            patch("shared_kernel.auth.clients.get_redis", return_value=mock_redis),
            patch("shared_kernel.queue.enqueue", new_callable=AsyncMock),
        ):
            outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.RUNNING
        assert outcome.park is True

    @patch("contexts.workflow.application.executors.instruct.interpolate", return_value="t")
    async def test_facade_exception_returns_failed(self, _interp) -> None:
        from contexts.workflow.application.executors.instruct import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.INSTRUCT,
            {
                "issuer_agent_id": str(uuid.uuid4()),
                "target_agent_id": str(uuid.uuid4()),
                "instruction_template": "t",
            },
        )

        facade_mock = AsyncMock()
        facade_mock.issue_instruct.side_effect = RuntimeError("connection lost")

        with patch(
            "contexts.orchestration.interfaces.facade.OrchestrationFacade",
            return_value=facade_mock,
        ):
            outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.FAILED
        assert outcome.port == "failure"
        assert "connection lost" in (outcome.error or "")


# ===========================================================================
# agent_invocation executor
# ===========================================================================


class TestAgentInvocationExecutor:
    @patch("contexts.workflow.application.executors.agent_invocation.interpolate", return_value="hello")
    @patch("contexts.workflow.application.executors.agent_invocation.audit.emit", new_callable=AsyncMock)
    async def test_successful_invocation(self, _audit, _interp) -> None:
        from contexts.workflow.application.executors.agent_invocation import execute

        ctx = _make_ctx()
        agent_id = str(uuid.uuid4())
        node = _make_node(
            NodeType.AGENT_INVOCATION,
            {
                "agent_id": agent_id,
                "input_template": "{{ task }}",
                "output_variable": "reply",
            },
        )

        facade_mock = AsyncMock()
        facade_mock.a2a_call.return_value = {"reply": "world"}

        with patch(
            "contexts.orchestration.interfaces.facade.OrchestrationFacade",
            return_value=facade_mock,
        ):
            outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert outcome.port == "success"
        assert outcome.output["reply"] == "world"
        assert ctx.variables["reply"] == "world"

    @patch("contexts.workflow.application.executors.agent_invocation.interpolate", return_value="hi")
    async def test_invocation_failure(self, _interp) -> None:
        from contexts.workflow.application.executors.agent_invocation import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.AGENT_INVOCATION,
            {"agent_id": str(uuid.uuid4()), "input_template": "t"},
        )

        facade_mock = AsyncMock()
        facade_mock.a2a_call.side_effect = RuntimeError("timeout")

        with patch(
            "contexts.orchestration.interfaces.facade.OrchestrationFacade",
            return_value=facade_mock,
        ):
            outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.FAILED
        assert outcome.port == "failure"
        assert "timeout" in (outcome.error or "")

    @patch("contexts.workflow.application.executors.agent_invocation.interpolate", return_value="hi")
    @patch("contexts.workflow.application.executors.agent_invocation.audit.emit", new_callable=AsyncMock)
    async def test_no_output_variable(self, _audit, _interp) -> None:
        from contexts.workflow.application.executors.agent_invocation import execute

        ctx = _make_ctx()
        node = _make_node(
            NodeType.AGENT_INVOCATION,
            {"agent_id": str(uuid.uuid4()), "input_template": "t"},
        )

        facade_mock = AsyncMock()
        facade_mock.a2a_call.return_value = {"reply": "ok"}

        with patch(
            "contexts.orchestration.interfaces.facade.OrchestrationFacade",
            return_value=facade_mock,
        ):
            outcome = await execute(ctx, node, AsyncMock())

        assert outcome.state == StepState.SUCCEEDED
        assert "reply" not in ctx.variables
