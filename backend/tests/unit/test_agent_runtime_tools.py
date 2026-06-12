"""K.2 — per-turn tool registry: update_wakeup, load_prompt_section, dispatch."""

from __future__ import annotations

import json
import uuid

import pytest

from contexts.agents.application.prompt_loader import LazyPrompt, SectionCache
from contexts.agents.application.runtime.tool_registry import (
    ToolRegistry,
    build_load_prompt_section_tool,
    build_registry,
    build_update_wakeup_tool,
)


@pytest.mark.asyncio()
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


@pytest.mark.asyncio()
async def test_load_prompt_section_tool_serves_and_caches() -> None:
    prompt = LazyPrompt(index="idx", bodies={"alpha": "body-A", "beta": "body-B"})
    cache = SectionCache()
    tool = build_load_prompt_section_tool(prompt, cache)

    res = await tool.invoke({"id": "alpha"})
    assert res.content == "body-A"
    assert not res.is_error
    assert cache.get("alpha") == "body-A"  # cached for the turn (R9.07)

    miss = await tool.invoke({"id": "ghost"})
    assert miss.is_error


@pytest.mark.asyncio()
async def test_registry_dispatch_and_unknown() -> None:
    reg = build_registry(object(), agent_id=uuid.uuid4())
    # update_wakeup is always present; load_prompt_section only with a lazy prompt.
    assert len(reg) == 1
    assert reg.get("update_wakeup") is not None
    assert {s["name"] for s in reg.specs()} == {"update_wakeup"}

    unknown = await ToolRegistry([]).call("nope", {})
    assert unknown.is_error


@pytest.mark.asyncio()
async def test_registry_includes_lazy_section_tool() -> None:
    prompt = LazyPrompt(index="idx", bodies={"a": "x"})
    reg = build_registry(
        object(), agent_id=uuid.uuid4(), lazy_prompt=prompt, section_cache=SectionCache()
    )
    assert {s["name"] for s in reg.specs()} == {"update_wakeup", "load_prompt_section"}


@pytest.mark.asyncio()
async def test_registry_swallows_tool_exception() -> None:
    from contexts.agents.application.runtime.tool_registry import Tool

    async def _boom(_args):
        raise RuntimeError("kaboom")

    reg = ToolRegistry([Tool(name="x", description="d", input_schema={}, invoke=_boom)])
    res = await reg.call("x", {})
    assert res.is_error
    assert "kaboom" in res.content
