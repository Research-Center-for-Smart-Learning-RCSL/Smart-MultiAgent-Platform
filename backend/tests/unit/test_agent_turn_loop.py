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
    is_truncated_finish_reason,
)
from contexts.keys.domain.errors import KeyGroupExhausted
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

    outcome = await engine._stream_with_tools(
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

    assert outcome.text == "done"
    assert outcome.rounds == 1  # exactly one tool round executed
    assert outcome.synthesis_failed is False
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

    outcome = await engine._stream_with_tools(
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
    assert outcome.text == "hi there"
    assert outcome.rounds == 0


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

    outcome = await engine._stream_with_tools(
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

    assert outcome.text == "final"
    assert outcome.rounds == te.MAX_TOOL_ROUNDS
    assert len(engine._router.requests) == te.MAX_TOOL_ROUNDS + 1  # type: ignore[attr-defined]
    final_request = engine._router.requests[-1]  # type: ignore[attr-defined]
    assert final_request.provider is ApiKeyProvider.CLAUDE
    assert final_request.payload["model"] == "claude-opus-4-8"
    assert "models" not in final_request.payload


class _TruncatingRouter:
    """Emits a tool call cut off at the output ceiling, `rounds` times over."""

    def __init__(self, truncated_rounds: int) -> None:
        self.requests: list = []
        self._truncated_rounds = truncated_rounds

    async def call_stream(self, *, group_id, request):
        self.requests.append(request)
        if len(self.requests) <= self._truncated_rounds:
            yield StreamComplete(
                ProviderCallResult(
                    200,
                    {
                        "text": "",
                        # What the adapters produce from unterminated argument
                        # JSON: `{}`, indistinguishable from a real empty call.
                        "tool_calls": [{"id": "t1", "name": "web_search", "arguments": {}}],
                        "finish_reason": "max_tokens",
                    },
                )
            )
        else:
            yield StreamComplete(
                ProviderCallResult(200, {"text": "ok", "tool_calls": [], "finish_reason": "end_turn"})
            )


@pytest.mark.asyncio
async def test_truncated_tool_arguments_are_not_dispatched() -> None:
    """AC-1/AC-5. `{}` from a truncated argument stream reached the tool, which
    coerced it: `web_search` searched for the empty string and reported a
    plausible empty result set as a success."""
    engine, agent = _engine_with(_TruncatingRouter(1))
    registry = _FakeRegistry()
    messages: list = [{"role": "user", "content": "search for something long"}]

    outcome = await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=messages,
        provider=ApiKeyProvider.CLAUDE,
        model="m",
        registry=registry,
        room=None,
    )

    assert registry.invoked == []
    tool_result = next(m for m in messages if m["role"] == "tool")
    # The content, not just is_error: OpenAI and Gemini drop is_error entirely.
    assert "not run" in tool_result["content"]
    assert "smaller" in tool_result["content"]
    assert tool_result["is_error"] is True
    # Anthropic requires a tool_result for every tool_use block it sent.
    assert tool_result["tool_call_id"] == "t1"
    assert outcome.text == "ok"


@pytest.mark.parametrize("reason", ["max_tokens", "length", "MAX_TOKENS"])
def test_every_provider_spelling_of_truncation_maps_to_one_value(reason: str) -> None:
    """Anthropic, OpenAI and Gemini each name it differently and the field is
    passed through verbatim, so the turn loop needs one predicate."""
    assert is_truncated_finish_reason(reason) is True


@pytest.mark.parametrize("reason", ["end_turn", "tool_use", "stop", "STOP", None, 3])
def test_a_normal_stop_is_not_truncation(reason) -> None:
    assert is_truncated_finish_reason(reason) is False


@pytest.mark.asyncio
async def test_a_rejected_round_does_not_cost_the_model_its_budget() -> None:
    """Charging a round the model did no work in makes "retry with a smaller
    payload" unusable advice, since the retry may have no budget to run in."""
    engine, agent = _engine_with(_TruncatingRouter(2))

    outcome = await _run_to_synthesis(engine, agent)

    # Two rejections, then a clean answer: no tool round was actually completed.
    assert outcome.rounds == 0
    assert outcome.text == "ok"
    assert len(engine._router.requests) == 3  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_model_that_truncates_forever_still_terminates() -> None:
    """The uncharged rounds are capped, or the loop never ends."""
    engine, agent = _engine_with(_TruncatingRouter(1000))

    outcome = await _run_to_synthesis(engine, agent)

    assert outcome.rounds == te.MAX_TOOL_ROUNDS
    assert len(engine._router.requests) == te.MAX_TOOL_ROUNDS + te._MAX_ARG_REJECTIONS + 1


class _EightRoundsThenRouter:
    """Burns every tool round, then hands the final synthesis call to `finish`."""

    def __init__(self, finish) -> None:
        self.requests: list = []
        self._finish = finish

    async def call_stream(self, *, group_id, request):
        self.requests.append(request)
        if len(self.requests) <= te.MAX_TOOL_ROUNDS:
            yield StreamComplete(
                ProviderCallResult(
                    200,
                    {
                        "text": "Let me check that for you.",
                        "tool_calls": [{"id": "t1", "name": "update_wakeup", "arguments": {}}],
                        "finish_reason": "tool_use",
                    },
                )
            )
            return
        async for ev in self._finish():
            yield ev


def _engine_with(router) -> tuple:
    engine = te.TurnEngine.__new__(te.TurnEngine)
    engine._router = router  # type: ignore[attr-defined]
    agent = SimpleNamespace(
        id=uuid.uuid4(), key_group_id=uuid.uuid4(), effort=None, temperature=None, top_p=None, seed=None
    )
    return engine, agent


async def _run_to_synthesis(engine, agent):
    return await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        provider=ApiKeyProvider.CLAUDE,
        model="m",
        registry=_FakeRegistry(),
        room=None,
    )


@pytest.mark.asyncio
async def test_synthesis_failure_is_reported_not_hidden() -> None:
    """AC-7. A provider failure on the final call used to be swallowed and the
    round-8 filler returned as the agent's answer, with a success audit."""

    async def _explode():
        raise KeyGroupExhausted(group_id=uuid.uuid4(), reason="no_usable_key")
        yield  # pragma: no cover - makes this an async generator

    engine, agent = _engine_with(_EightRoundsThenRouter(_explode))

    outcome = await _run_to_synthesis(engine, agent)

    assert outcome.synthesis_failed is True
    assert outcome.error_kind is not None
    assert outcome.error_kind.startswith("provider_exhausted")
    # The filler still comes back: eight rounds of real tool work stand behind it,
    # and losing that is worse than showing it flagged.
    assert outcome.text == "Let me check that for you."
    assert outcome.rounds == te.MAX_TOOL_ROUNDS


@pytest.mark.asyncio
async def test_synthesis_missing_terminal_event_is_reported() -> None:
    """AC-9. The call succeeds but no StreamComplete arrives, so there is no
    synthesised text. This fell through `final_body.get("text", last_text)` with
    no log line and no marker at all."""

    async def _tokens_only():
        yield TokenDelta("partial")

    engine, agent = _engine_with(_EightRoundsThenRouter(_tokens_only))

    outcome = await _run_to_synthesis(engine, agent)

    assert outcome.synthesis_failed is True
    assert outcome.error_kind == "provider_no_terminal_event"
    assert outcome.text == "Let me check that for you."


@pytest.mark.asyncio
async def test_a_successful_synthesis_carries_no_failure_marker() -> None:
    async def _answers():
        yield StreamComplete(ProviderCallResult(200, {"text": "the real answer", "tool_calls": []}))

    engine, agent = _engine_with(_EightRoundsThenRouter(_answers))

    outcome = await _run_to_synthesis(engine, agent)

    assert outcome.synthesis_failed is False
    assert outcome.error_kind is None
    assert outcome.text == "the real answer"


def test_the_synthesis_marker_is_absent_on_a_healthy_turn() -> None:
    """The reply metadata, the audit row and the WS event all splat this, so an
    empty dict is what keeps a healthy turn's records byte-identical."""
    assert te._synthesis_meta(te.ToolLoopOutcome(text="ok", rounds=2)) == {}
    assert te._synthesis_meta(
        te.ToolLoopOutcome(text="filler", rounds=8, synthesis_failed=True, error_kind="database_error")
    ) == {"synthesis_failed": True, "error": "database_error"}


@pytest.mark.asyncio
async def test_the_tool_rounds_and_the_final_call_are_built_the_same_way() -> None:
    """Characterization. Both requests are assembled from one builder; the only
    intended differences are the messages list and whether tools are offered.

    Two near-identical builds with their own copies of the effort and sampling
    handling is how they drift, and a sampling control silently dropped from the
    synthesis call is invisible in any assertion about text.
    """

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
        id=uuid.uuid4(),
        key_group_id=uuid.uuid4(),
        effort=SimpleNamespace(value="high"),
        temperature=0.3,
        top_p=0.9,
        seed=7,
    )

    await engine._stream_with_tools(
        agent=agent,
        chatroom_id=uuid.uuid4(),
        parent_agent_id=None,
        system_text="sys",
        messages=[{"role": "user", "content": "hi"}],
        provider=ApiKeyProvider.CLAUDE,
        model="m",
        registry=_FakeRegistry(),
        room=None,
    )

    tool_round, final = (
        engine._router.requests[0],  # type: ignore[attr-defined]
        engine._router.requests[-1],  # type: ignore[attr-defined]
    )
    for request in (tool_round, final):
        assert request.payload["effort"] == "high"
        assert request.payload["temperature"] == 0.3
        assert request.payload["top_p"] == 0.9
        assert request.payload["seed"] == 7
        assert request.payload["max_tokens"] == te._DEFAULT_MAX_TOKENS
        assert request.payload["system"] == "sys"
        assert request.capability is te.ProviderCapability.LLM_CHAT
    # The one intended difference: the final call offers no tools, so the
    # provider does not require the tool_use blocks that were stripped out.
    assert "tools" in tool_round.payload
    assert "tools" not in final.payload


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
