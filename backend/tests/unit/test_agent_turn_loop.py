"""K.2 — TurnEngine tool-use loop: stream tokens, run a tool round, resume.

Exercises the heart of the engine (`_stream_with_tools`) in isolation with a
fake streaming router and a fake registry, so the multi-round tool protocol is
covered without a live Postgres/Redis stack (that is the K.7 compose tier).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

import contexts.agents.application.runtime.transcript as tx
import contexts.agents.application.runtime.turn_engine as te
from contexts.agents.application.runtime.tool_registry import ToolResult
from contexts.keys.application.provider_router import (
    ProviderCallResult,
    StreamComplete,
    TokenDelta,
)
from contexts.keys.domain.providers import ApiKeyProvider


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


def test_resolve_provider_and_model_uses_agent_override_or_provider_default() -> None:
    agent = SimpleNamespace(model_hint=SimpleNamespace(value="claude"), model_id="claude-opus-4-8")

    provider, model = te._resolve_provider_and_model(agent)

    assert provider is ApiKeyProvider.CLAUDE
    assert model == "claude-opus-4-8"

    agent.model_id = None
    provider, model = te._resolve_provider_and_model(agent)

    assert provider is ApiKeyProvider.CLAUDE
    assert model == "claude-sonnet-4-6"


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
    agent = SimpleNamespace(
        id=uuid.uuid4(), key_group_id=uuid.uuid4(), effort=None, temperature=None, top_p=None, seed=None
    )
    messages: list = [{"role": "user", "content": "set my cadence"}]

    text, rounds = await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=messages,
        provider=ApiKeyProvider.CLAUDE,
        model="m",
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
    for request in engine._router.requests:  # type: ignore[attr-defined]
        assert request.provider is ApiKeyProvider.CLAUDE
        assert request.payload["model"] == "m"
        assert "models" not in request.payload


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
    agent = SimpleNamespace(
        id=uuid.uuid4(), key_group_id=uuid.uuid4(), effort=None, temperature=None, top_p=None, seed=None
    )

    text, rounds = await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        provider=ApiKeyProvider.CLAUDE,
        model="m",
        registry=_FakeRegistry(),
        room="room",
    )
    assert text == "hi there"
    assert rounds == 0


@pytest.mark.asyncio
async def test_final_no_tools_call_carries_the_same_provider_and_model() -> None:
    class _ToolRoundRouter:
        def __init__(self) -> None:
            self.requests: list = []

        async def call_stream(self, *, group_id, request):
            self.requests.append(request)
            if len(self.requests) <= te.MAX_TOOL_ROUNDS:
                yield StreamComplete(
                    ProviderCallResult(
                        200,
                        {
                            "text": "",
                            "tool_calls": [{"id": "t1", "name": "update_wakeup", "arguments": {}}],
                            "finish_reason": "tool_use",
                        },
                    )
                )
            else:
                yield StreamComplete(ProviderCallResult(200, {"text": "final"}))

    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._router = _ToolRoundRouter()  # type: ignore[attr-defined]
    agent = SimpleNamespace(
        id=uuid.uuid4(), key_group_id=uuid.uuid4(), effort=None, temperature=None, top_p=None, seed=None
    )

    text, rounds = await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        provider=ApiKeyProvider.CLAUDE,
        model="claude-opus-4-8",
        registry=_FakeRegistry(),
        room=None,
    )

    assert text == "final"
    assert rounds == te.MAX_TOOL_ROUNDS
    assert len(engine._router.requests) == te.MAX_TOOL_ROUNDS + 1  # type: ignore[attr-defined]
    final_request = engine._router.requests[-1]  # type: ignore[attr-defined]
    assert final_request.provider is ApiKeyProvider.CLAUDE
    assert final_request.payload["model"] == "claude-opus-4-8"
    assert "models" not in final_request.payload


def test_a_database_fault_is_classified_as_infrastructure() -> None:
    """The predicate the engine hands the registry (AC-3)."""
    assert te._is_infrastructure_error(OperationalError("stmt", {}, Exception("down"))) is True
    assert te._is_infrastructure_error(RuntimeError("a tool said no")) is False


def test_a_database_fault_never_puts_sql_in_the_reported_error_kind() -> None:
    """`_err_kind` output reaches the WS payload and the audit row, and a
    SQLAlchemy message can carry the failing SQL, table names and parameters."""
    exc = OperationalError("SELECT secret FROM api_keys WHERE id = 'k-123'", {}, Exception("down"))

    kind = te._err_kind(exc)

    assert kind == "database_error"
    assert "api_keys" not in kind
    assert "OperationalError" not in kind


def test_knowledge_queries_include_recent_context() -> None:
    current = "What risks does that plan have?"
    history = [
        SimpleNamespace(role="user", content="We are discussing the Q3 migration plan."),
        SimpleNamespace(role="agent", content="The plan moves billing first, then chat."),
        SimpleNamespace(role="user", content=current),
    ]

    queries = te._knowledge_queries(history, input_text=None)

    assert queries[0] == current
    assert len(queries) == 2
    assert "Q3 migration plan" in queries[1]
    assert "Current question:" in queries[1]


def test_knowledge_queries_include_compact_summary() -> None:
    history = [
        SimpleNamespace(role="system", content="Earlier: Alice chose vendor B for latency."),
        SimpleNamespace(role="user", content="Why did we choose them?"),
    ]

    queries = te._knowledge_queries(history, input_text=None)

    assert len(queries) == 2
    assert "Alice chose vendor B" in queries[1]


def _history_message(*, content, attachment_excerpt=None, sender_id=None):
    return tx.HistoryMessage(
        id=uuid.uuid4(),
        sender_id=sender_id,
        role="user",
        content=content,
        metadata={},
        token_count=1,
        attachment_excerpt=attachment_excerpt,
    )


def test_provider_message_folds_excerpt_when_no_live_attachment_blocks() -> None:
    hm = _history_message(
        content="what did the file say?", attachment_excerpt="[Attached file: a.txt]\nkey facts"
    )

    msg = te.TurnEngine._provider_message(hm, uuid.uuid4(), {}, {}, attachment_blocks=None)

    assert msg["content"] == "what did the file say?\n\n[Attached file: a.txt]\nkey facts"


def test_provider_message_suppresses_excerpt_when_live_attachment_blocks_present() -> None:
    # The triggering message carries rich vision/PDF blocks already — the
    # excerpt must not also be spliced in, or the file's content would be
    # shown to the model twice.
    hm = _history_message(content="analyze this", attachment_excerpt="[Attached file: a.txt]\nkey facts")
    blocks = [{"type": "document", "media_type": "application/pdf", "data": "abc", "filename": "a.pdf"}]

    msg = te.TurnEngine._provider_message(hm, uuid.uuid4(), {}, {}, attachment_blocks=blocks)

    assert msg["content"] == [{"type": "text", "text": "analyze this"}, *blocks]


def test_provider_message_no_excerpt_is_unchanged() -> None:
    hm = _history_message(content="plain message")

    msg = te.TurnEngine._provider_message(hm, uuid.uuid4(), {}, {}, attachment_blocks=None)

    assert msg == {"role": "user", "content": "plain message"}
