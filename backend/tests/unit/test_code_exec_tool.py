"""Unit tests for :class:`CodeExecTool` — E.11."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.agents.application.tools.code_exec import CodeExecTool
from contexts.agents.domain.mcp import ToolCallResult


class _FakeRunner:
    def __init__(self, result: ToolCallResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def run_code_exec(self, **kwargs: Any) -> ToolCallResult:
        self.calls.append(kwargs)
        return self.result

    async def probe(self, **_: Any):  # pragma: no cover
        raise NotImplementedError

    async def invoke_mcp_tool(self, **_: Any):  # pragma: no cover
        raise NotImplementedError

    async def run_file_op(self, **_: Any):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_run_captures_stdout_stderr_and_exit() -> None:
    expected = ToolCallResult(
        ok=True, stdout="42\n", stderr="",
        exit_code=0, duration_ms=12,
    )
    runner = _FakeRunner(expected)
    tool = CodeExecTool(agent_id=uuid.uuid4(), runner=runner)
    result = await tool.run("print(42)")
    assert result is expected
    assert runner.calls[0]["source"] == "print(42)"


@pytest.mark.asyncio
async def test_run_non_zero_exit_returned() -> None:
    expected = ToolCallResult(
        ok=False, stdout="", stderr="Traceback...",
        exit_code=1, duration_ms=8,
    )
    runner = _FakeRunner(expected)
    tool = CodeExecTool(agent_id=uuid.uuid4(), runner=runner)
    result = await tool.run("raise SystemExit(1)")
    assert result.ok is False
    assert result.exit_code == 1
    assert "Traceback" in result.stderr


@pytest.mark.asyncio
async def test_timeout_clamped_to_30s() -> None:
    runner = _FakeRunner(
        ToolCallResult(ok=True, stdout="", stderr="", exit_code=0, duration_ms=1),
    )
    tool = CodeExecTool(agent_id=uuid.uuid4(), runner=runner)
    await tool.run("print(1)", timeout_s=900)  # caller asked for 15 min
    assert runner.calls[0]["timeout_s"] == 30.0  # clamped


@pytest.mark.asyncio
async def test_timeout_accepts_shorter_budget() -> None:
    runner = _FakeRunner(
        ToolCallResult(ok=True, stdout="", stderr="", exit_code=0, duration_ms=1),
    )
    tool = CodeExecTool(agent_id=uuid.uuid4(), runner=runner)
    await tool.run("print(1)", timeout_s=5)
    assert runner.calls[0]["timeout_s"] == 5.0


@pytest.mark.asyncio
async def test_timeout_rejects_non_positive() -> None:
    tool = CodeExecTool(
        agent_id=uuid.uuid4(),
        runner=_FakeRunner(
            ToolCallResult(ok=True, stdout="", stderr="", exit_code=0, duration_ms=0),
        ),
    )
    with pytest.raises(ValueError):
        await tool.run("print(1)", timeout_s=0)
    with pytest.raises(ValueError):
        await tool.run("print(1)", timeout_s=-5)


@pytest.mark.asyncio
async def test_empty_source_rejected() -> None:
    tool = CodeExecTool(
        agent_id=uuid.uuid4(),
        runner=_FakeRunner(
            ToolCallResult(ok=True, stdout="", stderr="", exit_code=0, duration_ms=0),
        ),
    )
    with pytest.raises(ValueError):
        await tool.run("")
