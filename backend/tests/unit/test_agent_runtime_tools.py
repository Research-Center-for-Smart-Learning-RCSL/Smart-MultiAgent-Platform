"""K.2 — per-turn tool registry: update_wakeup, read_skill (§31), dispatch."""

from __future__ import annotations

import json
import uuid

import pytest

from contexts.agents.application.runtime.tool_registry import (
    _MAX_TOOL_OUTPUT,
    _SKILL_BODY_TOKEN_BUDGET,
    BUILTIN_TOOL_NAMES,
    ToolRegistry,
    build_read_skill_tool,
    build_registry,
    build_update_wakeup_tool,
)
from shared_kernel.tokens import estimate_tokens
from tests.unit.skill_fakes import make_skill


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
    reg = build_registry(object(), agent_id=uuid.uuid4(), skills=[])
    # update_wakeup is the only unconditional built-in: an agent with nothing bound
    # is not offered read_skill and pays no tokens for a tool it cannot use.
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


# --------------------------------------------------------------------------- #
# read_skill (§31)                                                             #
# --------------------------------------------------------------------------- #


async def _read(tool, **args) -> dict:
    res = await tool.invoke(args)
    assert not res.is_error, res.content
    return json.loads(res.content)


def test_read_skill_is_a_reserved_builtin_and_build_registry_builds_it() -> None:
    # AC-14. The `build_agent_tools` drift test cannot see this tool — read_skill is
    # built in tool_registry, not in builtin_tools — so its wiring is asserted here or
    # nowhere. The name matters twice over: BUILTIN_TOOL_NAMES is also what
    # agent_service derives its reserved-name guard from, so a user LOCAL_FUNCTION
    # called read_skill would otherwise shadow the real one.
    assert "read_skill" in BUILTIN_TOOL_NAMES

    reg = build_registry(object(), agent_id=uuid.uuid4(), skills=[make_skill()])

    assert reg.get("read_skill") is not None
    assert "read_skill" in {s["name"] for s in reg.specs()}


@pytest.mark.asyncio
async def test_read_skill_returns_the_body_from_the_snapshot() -> None:
    # AC-5. The tool holds the snapshot; there is no db argument it could query with.
    skill = make_skill(name="pdf-fill", body="# PDF Fill\n\nOpen the form, then fill it.")
    tool = build_read_skill_tool([skill])

    out = await _read(tool, name="pdf-fill")

    assert out == {"name": "pdf-fill", "body": "# PDF Fill\n\nOpen the form, then fill it."}


@pytest.mark.asyncio
async def test_read_skill_unknown_name_is_a_tool_error_not_a_raise() -> None:
    # AC-5. The model can misread the index; that must cost it a tool round, not the
    # user's turn. `is_error` goes back to the model, an exception aborts the loop.
    tool = build_read_skill_tool([make_skill(name="pdf-fill")])

    res = await tool.invoke({"name": "no-such-skill"})

    assert res.is_error
    assert "no-such-skill" in res.content
    assert "pdf-fill" in res.content  # names what it could have called instead


@pytest.mark.asyncio
async def test_read_skill_cannot_reach_a_skill_outside_the_snapshot() -> None:
    # The turn-time tap drops a skill whose containment now fails. If read_skill could
    # still resolve it, the tap would be decorative and the drop cosmetic.
    dropped = make_skill(name="secret-skill", body="the other tenant's instructions")
    tool = build_read_skill_tool([make_skill(name="pdf-fill")])

    res = await tool.invoke({"name": dropped.name})

    assert res.is_error
    assert "the other tenant's instructions" not in res.content


@pytest.mark.asyncio
async def test_read_skill_rejects_an_offset_outside_the_body() -> None:
    tool = build_read_skill_tool([make_skill(name="pdf-fill", body="short")])

    res = await tool.invoke({"name": "pdf-fill", "offset": 9_999})

    assert res.is_error


_LONG_BODIES = {
    "latin": "The quick brown fox jumps over the lazy dog. " * 4_000,
    # Split mid-CJK-run: CJK costs 1 token/char, so the same budget cuts at a
    # completely different offset — and a cut between two CJK characters must not be
    # smoothed over by the estimator's Latin path.
    "cjk": "把表格打開然後逐欄填寫並存檔。" * 3_000,
    # No surrogate pairs on the Python side, but an astral character is 4 UTF-8 bytes
    # and 1 str index: a byte-based cut would sever it, a character one cannot.
    "astral": "𝕏marks the spot. " * 3_000,
}


@pytest.mark.parametrize("label", list(_LONG_BODIES))
@pytest.mark.asyncio
async def test_read_skill_spans_reassemble_the_body_exactly(label: str) -> None:
    # AC-33. Walk the whole body through continuation calls and rebuild it.
    body = _LONG_BODIES[label]
    tool = build_read_skill_tool([make_skill(name="long-one", body=body)])

    spans: list[str] = []
    offset: int | None = 0
    seen: set[int] = set()
    while offset is not None:
        assert offset not in seen, "continuation offset repeated — the walk would never end"
        seen.add(offset)
        res = await tool.invoke({"name": "long-one", "offset": offset})
        assert not res.is_error, res.content
        # Measured on what the tool actually hands the loop, not on a re-dump: the
        # clip is applied to that exact string, and a re-dump with different
        # ensure_ascii would measure a string nothing ever sees.
        assert len(res.content) <= _MAX_TOOL_OUTPUT
        assert "[truncated]" not in res.content, "the byte clip severed the JSON"
        out = json.loads(res.content)
        spans.append(out["body"])
        assert estimate_tokens(out["body"]) <= _SKILL_BODY_TOKEN_BUDGET
        offset = out.get("truncated_at_offset")

    assert len(spans) > 1, "the fixture must actually exceed the budget or it proves nothing"
    # No gap, no repeat. Deliberately NOT asserted: that the spans' estimates sum to
    # the whole body's. estimate_tokens is max(1, cjk + latin // 4) — non-additive by
    # construction, so that sum is not a property of correct code.
    assert "".join(spans) == body


@pytest.mark.asyncio
async def test_read_skill_body_within_budget_is_not_truncated() -> None:
    tool = build_read_skill_tool([make_skill(name="short-one", body="a" * 200)])

    out = await _read(tool, name="short-one")

    assert "truncated_at_offset" not in out
    assert out["body"] == "a" * 200
