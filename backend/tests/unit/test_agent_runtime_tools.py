"""K.2 — per-turn tool registry: update_wakeup, dispatch."""

from __future__ import annotations

import json
import uuid

import pytest

from contexts.agents.application.runtime.tool_registry import (
    ToolRegistry,
    build_registry,
    build_update_wakeup_tool,
)


@pytest.mark.asyncio
async def test_update_wakeup_tool_invokes_facade(monkeypatch) -> None:
    captured: dict = {}

    class _Cfg:
        def to_dict(self) -> dict:
            return {"triggers": {"every_n_messages": {"n": 3}}}

    class _Facade:
        def __init__(self, _db) -> None:
            pass

        async def update_wakeup(self, *, agent_id, every_n_messages, silence_minutes, actor_agent_id):
            captured.update(
                agent_id=agent_id,
                every_n_messages=every_n_messages,
                silence_minutes=silence_minutes,
                actor_agent_id=actor_agent_id,
            )
            return _Cfg()

    import contexts.orchestration.interfaces.facade as facade_mod

    monkeypatch.setattr(facade_mod, "OrchestrationFacade", _Facade)
    agent_id = uuid.uuid4()
    tool = build_update_wakeup_tool(object(), agent_id=agent_id)

    res = await tool.invoke({"every_n_messages": 3})

    assert not res.is_error
    assert json.loads(res.content) == {"triggers": {"every_n_messages": {"n": 3}}}
    assert captured["every_n_messages"] == 3
    assert captured["silence_minutes"] is None
    assert captured["agent_id"] == agent_id
    assert captured["actor_agent_id"] == agent_id


@pytest.mark.asyncio
async def test_registry_dispatch_and_unknown() -> None:
    reg = build_registry(object(), agent_id=uuid.uuid4())
    # update_wakeup is the only unconditional built-in.
    assert len(reg) == 1
    assert reg.get("update_wakeup") is not None
    assert {s["name"] for s in reg.specs()} == {"update_wakeup"}

    unknown = await ToolRegistry([]).call("nope", {})
    assert unknown.is_error


@pytest.mark.asyncio
async def test_registry_swallows_tool_exception() -> None:
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _boom(_args):
        raise RuntimeError("kaboom")

    reg = ToolRegistry([Tool(name="x", description="d", input_schema={}, invoke=_boom)])
    res = await reg.call("x", {})
    assert res.is_error
    assert "kaboom" in res.content


@pytest.mark.asyncio
async def test_registry_first_registration_wins_on_duplicate() -> None:
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _a(_args):
        from contexts.agents.application.runtime.tool_registry import ToolResult

        return ToolResult(content="A")

    async def _b(_args):
        from contexts.agents.application.runtime.tool_registry import ToolResult

        return ToolResult(content="B")

    reg = ToolRegistry(
        [
            Tool(name="dup", description="d", input_schema={}, invoke=_a),
            Tool(name="dup", description="d", input_schema={}, invoke=_b),
        ]
    )
    assert len(reg) == 1
    res = await reg.call("dup", {})
    assert res.content == "A"  # the first registration is kept, the shadow dropped
