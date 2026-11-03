"""The provider's output structure reaches the turn's audit row ([R13.80]).

`output_shape` is how a recurrence of the phase 6 leak is told apart from a model
writing notes inside its own final answer: it lists the final response's item kinds,
their phase and text lengths, never text. See
docs/tasks/2026-11-03-crat-facilitation-phase6-reply-commentary-leak/spec.md.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import contexts.agents.application.runtime.turn_engine as te
from contexts.agents.application.runtime.tool_registry import ToolResult
from contexts.keys.application.provider_router import ProviderCallResult, StreamComplete, TokenDelta
from contexts.keys.domain.providers import ApiKeyProvider
from tests.unit.turn_engine_fakes import make_agent, run_locked, wire_engine

_NOTE = {"kind": "notify", "body": "unseen"}
_SHAPE = [
    {"type": "reasoning"},
    {"type": "message", "phase": "commentary", "chars": 12},
    {"type": "message", "phase": "final_answer", "chars": 4},
]


def _replies(text: str, shape: list[dict[str, Any]] | None):
    async def _stream(**kw: Any) -> te.ToolLoopOutcome:
        return te.ToolLoopOutcome(text=text, rounds=0, output_shape=shape)

    return _stream


async def _none(*a: Any, **k: Any) -> None:
    return None


class TestTheRoomTurnAuditsTheShape:
    async def test_a_posted_reply_carries_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = make_agent(), uuid.uuid4()
        engine, trace = wire_engine(monkeypatch, agent, note=_NOTE)
        engine._stream_with_tools = _replies("答對了。", _SHAPE)

        result = await run_locked(engine, room, agent)

        assert result.status == "completed"
        assert trace.audited("agent.turn_finished")[-1]["output_shape"] == _SHAPE

    async def test_an_empty_reply_skip_carries_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A commentary-only response normalises to an empty reply (Q-2); the shape is
        # what shows the audit reader why the turn said nothing.
        agent, room = make_agent(), uuid.uuid4()
        engine, trace = wire_engine(monkeypatch, agent, note=_NOTE)
        engine._stream_with_tools = _replies("", _SHAPE[:2])

        result = await run_locked(engine, room, agent)

        assert result.status == "skipped"
        finished = trace.audited("agent.turn_finished")[-1]
        assert finished["reason"] == "empty_reply"
        assert finished["output_shape"] == _SHAPE[:2]

    async def test_an_observer_turn_carries_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from contexts.conversation.domain.models import ChatroomAgentRole

        agent, room = make_agent(), uuid.uuid4()
        engine, trace = wire_engine(monkeypatch, agent, note=_NOTE, role=ChatroomAgentRole.OBSERVER)
        engine._stream_with_tools = _replies("a note", _SHAPE)
        engine._observer_memory_block = _none
        engine._emit_observation_event = _none

        async def _record(**kw: Any) -> SimpleNamespace:
            return SimpleNamespace(id=uuid.uuid4(), created_at=None)

        monkeypatch.setattr(te, "ObservationService", lambda db: SimpleNamespace(record=_record))

        await run_locked(engine, room, agent)

        finished = trace.audited("agent.turn_finished")[-1]
        assert finished["observer"] is True
        assert finished["output_shape"] == _SHAPE

    async def test_a_provider_without_one_leaves_the_key_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, room = make_agent(), uuid.uuid4()
        engine, trace = wire_engine(monkeypatch, agent, note=_NOTE)
        engine._stream_with_tools = _replies("the answer", None)

        await run_locked(engine, room, agent)

        assert "output_shape" not in trace.audited("agent.turn_finished")[-1]


class _ShapedRouter:
    """A tool round, then a final answer; each response reports its own shape."""

    def __init__(self) -> None:
        self.rounds = 0

    async def call_stream(self, *, group_id: Any, request: Any) -> Any:
        self.rounds += 1
        if self.rounds == 1:
            yield StreamComplete(
                ProviderCallResult(
                    200,
                    {
                        "text": "",
                        "tool_calls": [{"id": "t1", "name": "noop", "arguments": {}}],
                        "finish_reason": "tool_use",
                        "output_shape": [{"type": "function_call"}],
                    },
                )
            )
        else:
            yield TokenDelta("done")
            yield StreamComplete(
                ProviderCallResult(
                    200,
                    {"text": "done", "tool_calls": [], "finish_reason": "end", "output_shape": _SHAPE},
                )
            )


class _OneToolRegistry:
    def specs(self) -> list[dict[str, Any]]:
        return [{"name": "noop", "description": "d", "input_schema": {}}]

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        return ToolResult(content='{"ok": true}')


class _QuietPublisher:
    def __init__(self, channel: Any) -> None:
        pass

    async def emit(self, etype: str, data: Any = None) -> None:
        return None


_ROUND_SHAPE = [{"type": "message", "phase": "commentary", "chars": 26}, {"type": "function_call"}]


class _RoundsThenFailingSynthesis:
    """Every tool round reports a shape; the final no-tools call then fails."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_stream(self, *, group_id: Any, request: Any) -> Any:
        self.calls += 1
        if self.calls > te.MAX_TOOL_ROUNDS:
            raise RuntimeError("provider down")
        yield StreamComplete(
            ProviderCallResult(
                200,
                {
                    "text": "Let me check that for you.",
                    "tool_calls": [{"id": "t1", "name": "noop", "arguments": {}}],
                    "finish_reason": "tool_use",
                    "output_shape": _ROUND_SHAPE,
                },
            )
        )


def _loop_agent() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        effort=None,
        temperature=None,
        top_p=None,
        seed=None,
        custom_capabilities=None,
    )


async def test_a_failed_synthesis_keeps_the_last_rounds_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    # The posted text is the last round's, so that round's structure is what explains it.
    monkeypatch.setattr(te, "Publisher", _QuietPublisher)
    engine: Any = te.TurnEngine.__new__(te.TurnEngine)
    engine._router = _RoundsThenFailingSynthesis()

    outcome = await engine._stream_with_tools(
        agent=_loop_agent(),
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        provider=ApiKeyProvider.OPENAI,
        model="gpt-6-luna",
        registry=_OneToolRegistry(),
        room=None,
    )

    assert outcome.synthesis_failed is True
    assert outcome.text == "Let me check that for you."
    assert outcome.output_shape == _ROUND_SHAPE


async def test_the_loop_keeps_the_final_responses_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(te, "Publisher", _QuietPublisher)
    engine: Any = te.TurnEngine.__new__(te.TurnEngine)
    engine._router = _ShapedRouter()
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        effort=None,
        temperature=None,
        top_p=None,
        seed=None,
        custom_capabilities=None,
    )

    outcome = await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        provider=ApiKeyProvider.OPENAI,
        model="gpt-6-luna",
        registry=_OneToolRegistry(),
        room="room",
    )

    assert outcome.text == "done"
    assert outcome.output_shape == _SHAPE
