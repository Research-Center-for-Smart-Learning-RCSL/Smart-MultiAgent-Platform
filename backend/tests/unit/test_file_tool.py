"""Unit tests for :class:`FileTool` — E.11."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from contexts.agents.application.tools.file_tool import FileTool
from contexts.agents.domain.mcp import ToolCallResult


class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_file_op(self, **kwargs: Any) -> ToolCallResult:
        self.calls.append(kwargs)
        return ToolCallResult(
            ok=True, stdout="ok", stderr="",
            exit_code=0, duration_ms=1,
            metadata={"path": kwargs["path"]},
        )

    async def probe(self, **_: Any):  # pragma: no cover
        raise NotImplementedError

    async def invoke_mcp_tool(self, **_: Any):  # pragma: no cover
        raise NotImplementedError

    async def run_code_exec(self, **_: Any):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_volume_name_format() -> None:
    agent_id = uuid.uuid4()
    tool = FileTool(agent_id=agent_id, runner=_FakeRunner())
    assert tool.volume_name == f"smap-agent-fs-{agent_id}"


@pytest.mark.asyncio
async def test_list_delegates_to_runner() -> None:
    runner = _FakeRunner()
    agent_id = uuid.uuid4()
    tool = FileTool(agent_id=agent_id, runner=runner)
    result = await tool.list_("subdir")
    assert result.ok
    call = runner.calls[0]
    assert call["op"] == "list"
    assert call["path"] == "/workspace/subdir"
    assert call["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_read_delegates_with_absolute_path() -> None:
    runner = _FakeRunner()
    tool = FileTool(agent_id=uuid.uuid4(), runner=runner)
    await tool.read("/workspace/a/b.txt")
    assert runner.calls[0]["path"] == "/workspace/a/b.txt"
    assert runner.calls[0]["op"] == "read"


@pytest.mark.asyncio
async def test_write_delegates_with_bytes() -> None:
    runner = _FakeRunner()
    tool = FileTool(agent_id=uuid.uuid4(), runner=runner)
    await tool.write("out.txt", b"hello")
    c = runner.calls[0]
    assert c["op"] == "write"
    assert c["data"] == b"hello"
    assert c["path"] == "/workspace/out.txt"


@pytest.mark.asyncio
async def test_path_traversal_rejected() -> None:
    tool = FileTool(agent_id=uuid.uuid4(), runner=_FakeRunner())
    for bad in ["../etc/passwd", "/etc/passwd", "../../..", "foo/../../bar"]:
        with pytest.raises(ValueError):
            await tool.read(bad)


@pytest.mark.asyncio
async def test_null_byte_rejected() -> None:
    tool = FileTool(agent_id=uuid.uuid4(), runner=_FakeRunner())
    with pytest.raises(ValueError):
        await tool.read("foo\x00bar")


@pytest.mark.asyncio
async def test_write_requires_bytes() -> None:
    tool = FileTool(agent_id=uuid.uuid4(), runner=_FakeRunner())
    with pytest.raises(TypeError):
        await tool.write("a.txt", "not-bytes")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_write_size_cap() -> None:
    tool = FileTool(agent_id=uuid.uuid4(), runner=_FakeRunner())
    with pytest.raises(ValueError):
        await tool.write("big.bin", b"x" * (10 * 1024 * 1024 + 1))
