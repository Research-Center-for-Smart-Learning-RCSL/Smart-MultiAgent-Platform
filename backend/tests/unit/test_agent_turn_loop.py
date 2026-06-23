"""K.2 — TurnEngine tool-use loop: stream tokens, run a tool round, resume.

Exercises the heart of the engine (`_stream_with_tools`) in isolation with a
fake streaming router and a fake registry, so the multi-round tool protocol is
covered without a live Postgres/Redis stack (that is the K.7 compose tier).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import contexts.agents.application.runtime.turn_engine as te
from contexts.agents.application.runtime.tool_registry import ToolResult
from contexts.keys.application.provider_router import (
    ProviderCallResult,
    StreamComplete,
    TokenDelta,
)


class _FakeRouter:
    """call_stream yields a tool_use round, then a plain-text completion."""

    def __init__(self) -> None:
        self.rounds = 0
        self.requests: list = []

    async def call_stream(self, *, group_id, request):
        self.rounds += 1
        self.requests.append(request)
        if self.rounds == 1:
            yield TokenDelta("think")
            yield StreamComplete(
                ProviderCallResult(
                    200,
                    {
                        "text": "",
                        "tool_calls": [
                            {"id": "t1", "name": "update_wakeup", "arguments": {"every_n_messages": 3}}
                        ],
                        "finish_reason": "tool_use",
                    },
                )
            )
        else:
            yield TokenDelta("done")
            yield StreamComplete(
                ProviderCallResult(200, {"text": "done", "tool_calls": [], "finish_reason": "end"})
            )


class _FakeRegistry:
    def __init__(self) -> None:
        self.invoked: list = []

    def specs(self):
        return [{"name": "update_wakeup", "description": "d", "input_schema": {}}]

    async def call(self, name, args):
        self.invoked.append((name, args))
        return ToolResult(content='{"ok": true}')


@pytest.mark.asyncio
async def test_stream_with_tools_runs_one_tool_round(monkeypatch) -> None:
    events: list = []

    class _Pub:
        def __init__(self, channel) -> None:
            self.channel = channel

        async def emit(self, etype, data=None):
            events.append((etype, data))

    monkeypatch.setattr(te, "Publisher", _Pub)

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._router = _FakeRouter()  # type: ignore[attr-defined]
    registry = _FakeRegistry()
    agent = SimpleNamespace(id=uuid.uuid4(), key_group_id=uuid.uuid4())
    messages: list = [{"role": "user", "content": "set my cadence"}]

    text, rounds = await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=messages,
        models={"claude": "m"},
        registry=registry,
        room="room",
    )

    assert text == "done"
    assert rounds == 1  # exactly one tool round executed
    assert registry.invoked == [("update_wakeup", {"every_n_messages": 3})]

    # The conversation grew by: assistant tool_use turn + tool result.
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["name"] == "update_wakeup"
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "t1"
    assert messages[2]["name"] == "update_wakeup"

    # Streamed deltas were published (payloads carry the agent_id).
    aid = str(agent.id)
    assert ("agent.token", {"text": "think", "agent_id": aid}) in events
    assert ("agent.token", {"text": "done", "agent_id": aid}) in events

    # The second request carried the tool result back to the provider.
    assert engine._router.rounds == 2  # type: ignore[attr-defined]
    assert engine._router.requests[1].payload["messages"][-1]["role"] == "tool"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stream_with_tools_no_tools_single_round(monkeypatch) -> None:
    class _PlainRouter:
        async def call_stream(self, *, group_id, request):
            yield TokenDelta("hi")
            yield StreamComplete(
                ProviderCallResult(200, {"text": "hi there", "tool_calls": [], "finish_reason": "end"})
            )

    class _Pub:
        def __init__(self, channel) -> None:
            pass

        async def emit(self, etype, data=None):
            return None

    monkeypatch.setattr(te, "Publisher", _Pub)
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._router = _PlainRouter()  # type: ignore[attr-defined]
    agent = SimpleNamespace(id=uuid.uuid4(), key_group_id=uuid.uuid4())

    text, rounds = await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        models={"claude": "m"},
        registry=_FakeRegistry(),
        room="room",
    )
    assert text == "hi there"
    assert rounds == 0
