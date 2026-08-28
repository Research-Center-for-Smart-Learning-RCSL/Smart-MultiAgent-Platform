"""K.1 — provider adapters: request shaping, normalisation, streaming, scrubbing.

Every test asserts the secret never leaks into the normalised body, and that
the model id is taken from the caller's payload (never hardcoded). Streaming
tests assert chunk reassembly + usage capture. Gemini tests assert the key
rides the ``x-goog-api-key`` header, never the query string (K.1 contract).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from contexts.keys.application.provider_router import (
    ProviderRequest,
    StreamComplete,
    TokenDelta,
    is_truncated_finish_reason,
)
from contexts.keys.domain.providers import ProviderCapability
from contexts.keys.infrastructure.adapters.anthropic import AnthropicAdapter
from contexts.keys.infrastructure.adapters.cohere import CohereAdapter
from contexts.keys.infrastructure.adapters.gemini import GeminiAdapter
from contexts.keys.infrastructure.adapters.openai import OpenAIAdapter, _key_tag
from contexts.keys.infrastructure.adapters.voyage import VoyageAdapter

_SECRET = "sk-super-secret-key-value"


def _chat(model: str, **extra: object) -> ProviderRequest:
    payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}
    return ProviderRequest(capability=ProviderCapability.LLM_CHAT, payload=payload)


def _sse(*objs: dict, done: bool = False) -> httpx.Response:
    body = "".join(f"data: {json.dumps(o)}\n\n" for o in objs)
    if done:
        body += "data: [DONE]\n\n"  # Anthropic/Chat Completions terminal sentinel
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


# OpenAI chat runs on the Responses API; only embeddings stayed where they were.
_RESPONSES_URL = "https://api.openai.com/v1/responses"


def _text_item(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


def _call_item(call_id: str, name: str, arguments: str) -> dict:
    # `call_id` is what the next request's function_call_output must echo; `id`
    # is the item's own handle and is deliberately different here so a test
    # reading the wrong one fails.
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _response_obj(**over: object) -> dict:
    """A minimal successful `/v1/responses` response object."""
    data: dict = {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "output": [_text_item("ok")],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    data.update(over)
    return data


# --------------------------------------------------------------------------- #
# Anthropic                                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_anthropic_invoke_normalises_and_uses_payload_model() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200,
        json={
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 11, "output_tokens": 7},
        },
    )
    res = await AnthropicAdapter().invoke(secret=_SECRET, request=_chat("claude-opus-4-8"))
    assert res.http_status == 200
    assert res.body["text"] == "Hello"
    assert res.body["tool_calls"] == [{"id": "t1", "name": "lookup", "arguments": {"q": "x"}}]
    assert res.input_tokens == 11
    assert res.output_tokens == 7
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "claude-opus-4-8"  # caller-supplied, not hardcoded
    assert route.calls.last.request.headers["x-api-key"] == _SECRET


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_stream_reassembles_tokens_and_usage() -> None:
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
        )
    )
    events = [e async for e in AnthropicAdapter().stream(secret=_SECRET, request=_chat("claude-x"))]
    deltas = [e.text for e in events if isinstance(e, TokenDelta)]
    final = events[-1]
    assert deltas == ["Hel", "lo"]
    assert isinstance(final, StreamComplete)
    assert final.result.body["text"] == "Hello"
    assert final.result.body["finish_reason"] == "end_turn"
    assert final.result.input_tokens == 5
    assert final.result.output_tokens == 2


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_stream_assembles_tool_call() -> None:
    # tool_use input arrives as concatenated input_json_delta fragments.
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "t1", "name": "lookup"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"q"'},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": ': "x"}'},
            },
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}},
        )
    )
    events = [e async for e in AnthropicAdapter().stream(secret=_SECRET, request=_chat("claude-x"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.body["tool_calls"] == [{"id": "t1", "name": "lookup", "arguments": {"q": "x"}}]
    assert final.result.body["finish_reason"] == "tool_use"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_truncated_tool_json_is_empty_but_flagged_by_finish_reason() -> None:
    """The same closing fragment as the test above, omitted.

    `_safe_json` cannot do better than `{}` here — there is no valid document to
    parse — so `finish_reason` is the *only* thing separating a call whose
    arguments were cut off from one that legitimately had none. It carried that
    distinction and no consumer read it, which is defect B.
    """
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "t1", "name": "lookup"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"q"'},
            },
            {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
        )
    )
    events = [e async for e in AnthropicAdapter().stream(secret=_SECRET, request=_chat("claude-x"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.body["tool_calls"] == [{"id": "t1", "name": "lookup", "arguments": {}}]
    assert is_truncated_finish_reason(final.result.body["finish_reason"]) is True


@pytest.mark.asyncio
@respx.mock
async def test_openai_truncated_tool_json_is_empty_but_flagged_by_finish_reason() -> None:
    # The Responses API has no `finish_reason`: truncation is `status:
    # "incomplete"` plus `incomplete_details.reason`, which the adapter passes
    # through verbatim for the router to normalise.
    respx.post(_RESPONSES_URL).mock(
        return_value=_sse(
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"x"'},
            {
                "type": "response.incomplete",
                "response": _response_obj(
                    status="incomplete",
                    incomplete_details={"reason": "max_output_tokens"},
                    output=[_call_item("c1", "f", '{"x"')],
                ),
            },
        )
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.body["tool_calls"] == [{"id": "c1", "name": "f", "arguments": {}}]
    assert final.result.body["finish_reason"] == "max_output_tokens"
    assert is_truncated_finish_reason(final.result.body["finish_reason"]) is True


def test_a_legitimately_empty_tool_call_is_not_read_as_truncated() -> None:
    """The pair to the two above: identical `arguments`, opposite meaning."""
    assert is_truncated_finish_reason("tool_use") is False
    assert is_truncated_finish_reason("tool_calls") is False
    assert is_truncated_finish_reason("completed") is False


def test_every_providers_truncation_spelling_is_recognised() -> None:
    # One frozenset serves three providers and two OpenAI endpoints; a spelling
    # missing from it silently turns a cut-off tool call into a legitimate one.
    for reason in ("max_tokens", "length", "max_output_tokens", "MAX_TOKENS"):
        assert is_truncated_finish_reason(reason) is True


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("etype", "status"),
    [("overloaded_error", 529), ("rate_limit_error", 429), ("api_error", 500)],
)
async def test_anthropic_in_stream_error_maps_to_non_2xx(etype: str, status: int) -> None:
    # HTTP 200 + `{"type": "error", ...}` mid-stream must NOT pass as success.
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
            {"type": "error", "error": {"type": etype, "message": f"key {_SECRET} oops"}},
        )
    )
    events = [e async for e in AnthropicAdapter().stream(secret=_SECRET, request=_chat("claude-x"))]
    assert len(events) == 1  # no token deltas, exactly one terminal event
    final = events[0]
    assert isinstance(final, StreamComplete)
    assert final.result.http_status == status
    assert final.result.body == {"error": f"HTTP {status} ({etype})"}
    assert _SECRET not in json.dumps(final.result.body)  # scrubbed


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_skips_empty_assistant_history_message() -> None:
    # `{"role": "assistant", "content": ""}` would 400 deterministically and
    # burn every key via rotation — it must be dropped from the request.
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={
            "model": "claude-x",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": ""},  # no tool_calls, no text
                {"role": "user", "content": "still there?"},
            ],
        },
    )
    await AnthropicAdapter().invoke(secret=_SECRET, request=req)
    msgs = json.loads(route.calls.last.request.content)["messages"]
    assert [m["role"] for m in msgs] == ["user", "user"]
    assert all(m["content"] for m in msgs)


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_error_is_scrubbed() -> None:
    respx.post("https://api.anthropic.com/v1/messages").respond(
        400,
        json={"error": {"type": "invalid_request_error", "message": f"key {_SECRET} rejected"}},
    )
    res = await AnthropicAdapter().invoke(secret=_SECRET, request=_chat("claude-x"))
    assert res.http_status == 400
    assert res.body == {"error": "HTTP 400 (type=invalid_request_error)"}
    assert _SECRET not in json.dumps(res.body)  # no key reflection


@pytest.mark.asyncio
@respx.mock
async def test_openai_error_keeps_the_code_and_param_that_name_the_cause() -> None:
    # `type` alone is `invalid_request_error` for both "no such model" and
    # "parameter unsupported on this model"; `code`/`param` are what tell an
    # operator which one they hit. A gpt-5.4 agent with `effort` set produced
    # exactly this and reached the room as an unqualified "the run failed".
    respx.post(_RESPONSES_URL).respond(
        400,
        json={
            "error": {
                "type": "invalid_request_error",
                "code": "unsupported_parameter",
                "param": "reasoning_effort",
                "message": f"key {_SECRET} cannot use this",
            }
        },
    )
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-5.4"))
    assert res.http_status == 400
    assert res.body == {
        "error": "HTTP 400 (type=invalid_request_error; code=unsupported_parameter; param=reasoning_effort)"
    }
    assert _SECRET not in json.dumps(res.body)


_TOOL = {"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {}}}


def _ok_chat_route() -> object:
    return respx.post(_RESPONSES_URL).respond(200, json=_response_obj())


@pytest.mark.asyncio
@respx.mock
async def test_openai_drops_reasoning_effort_when_a_conflicting_model_carries_tools() -> None:
    # The gate still honours a model that declares the conflict, even though no
    # catalogued OpenAI row does any more (the conflict was a Chat Completions
    # behaviour). Kept as a guard: a future row -- or a future provider -- that
    # sets the flag must not have this adapter silently ignore it.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET,
        request=_chat(
            "gpt-5.4",
            effort="low",
            tools=[_TOOL],
            effort_values=("low", "medium", "high"),
            effort_conflicts_with_tools=True,
        ),
    )
    sent = json.loads(route.calls.last.request.content)
    assert "reasoning" not in sent
    assert sent["tools"]  # the tools themselves are still sent


@pytest.mark.asyncio
@respx.mock
async def test_the_incident_agent_scenario_now_keeps_its_reasoning_effort() -> None:
    # The inverse of the assertion this test carried before the migration. An
    # agent configured exactly as 結書 (the incident agent) was -- model_hint:
    # openai, model_id: gpt-5.4, effort: low, tools bound -- had to lose its
    # effort on /v1/chat/completions or 400 on every turn. On /v1/responses the
    # pair is accepted, which is the capability this migration exists to
    # deliver. Driven by the real capability table, not hand-set flags, so a
    # future edit to gpt-5.4's row is what this pins against.
    from contexts.agents.domain.model_specs import capability_fields, resolve_spec

    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET,
        request=_chat(
            "gpt-5.4", effort="low", tools=[_TOOL], **capability_fields(resolve_spec("openai", "gpt-5.4"))
        ),
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["reasoning"] == {"effort": "low"}
    assert sent["tools"]


@pytest.mark.asyncio
@respx.mock
async def test_openai_drops_an_effort_value_the_models_spec_does_not_list() -> None:
    # `agent.effort` is stored independently of `model_id` (R9.03a), so an
    # agent can carry a value its *current* model's spec never listed -- a
    # stale value from a previous model_id, or one set before the row
    # narrowed. Forwarding on `accepts_effort` alone (accepts effort AT ALL)
    # rather than membership in `effort_values` would reproduce this task's
    # own incident through the one channel the widened `AgentEffort` enum
    # opened. `accepts_effort=True` alone, with no matching `effort_values`
    # entry, must not be enough to forward.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET,
        request=_chat(
            "gpt-5.4",
            effort="xhigh",
            effort_values=("low", "medium", "high"),
            effort_conflicts_with_tools=False,
        ),
    )
    assert "reasoning" not in json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_drops_an_effort_value_the_models_spec_does_not_list() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(
        secret=_SECRET,
        request=_chat("claude-opus-4-8", effort="xhigh", effort_values=("low", "medium", "high")),
    )
    assert "output_config" not in json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
@respx.mock
async def test_gemini_drops_an_effort_value_the_models_spec_does_not_list() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    await GeminiAdapter().invoke(
        secret=_SECRET,
        request=_chat("gemini-x", effort="xhigh", effort_values=("low", "medium", "high")),
    )
    sent = json.loads(route.calls.last.request.content)
    assert "thinkingConfig" not in sent.get("generationConfig", {})


@pytest.mark.asyncio
@respx.mock
async def test_openai_conflicting_model_drops_reasoning_effort_even_without_tools_in_this_call() -> None:
    # AC-16: a turn's tool rounds and its tools-free synthesis call must produce
    # the *same* shape, or the turn reasons at the provider default and only
    # composes its final answer at the configured level (§4.3a). Resolving the
    # conflict from (provider, model) alone -- never from whether this specific
    # call happens to carry tools -- is what keeps the two calls identical.
    flags = {"effort_values": ("low", "medium", "high"), "effort_conflicts_with_tools": True}
    with_tools = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET, request=_chat("gpt-5.4", effort="low", tools=[_TOOL], **flags)
    )
    sent_with_tools = json.loads(with_tools.calls.last.request.content)
    respx.clear()
    without_tools = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-5.4", effort="low", **flags))
    sent_without_tools = json.loads(without_tools.calls.last.request.content)
    assert "reasoning" not in sent_with_tools
    assert "reasoning" not in sent_without_tools


@pytest.mark.asyncio
@respx.mock
async def test_openai_keeps_reasoning_effort_with_tools_on_a_non_conflicting_model() -> None:
    # A model whose spec accepts effort without a tools conflict must not lose
    # the setting just because tools are present on this call.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET,
        request=_chat(
            "o3",
            effort="high",
            tools=[_TOOL],
            effort_values=("low", "medium", "high"),
            effort_conflicts_with_tools=False,
        ),
    )
    assert json.loads(route.calls.last.request.content)["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
@respx.mock
async def test_openai_drops_reasoning_effort_on_a_model_absent_from_the_table() -> None:
    # A model id outside the capability table (Q-2's conservative floor) carries
    # no capability flags at all -- the payload built for it must contain no
    # optional parameter, `reasoning_effort` included, rather than guess from
    # the id the way the deleted regexes did.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-6.1", effort="low", tools=[_TOOL]))
    sent = json.loads(route.calls.last.request.content)
    assert "reasoning" not in sent
    assert "temperature" not in sent
    assert "top_p" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_drops_sampling_and_effort_for_a_model_absent_from_the_table() -> None:
    # Q-2's conservative floor: no capability flags attached (the id is not in
    # the table) means no optional parameter is sent, regardless of how the id
    # is spelled -- there is no longer a family pattern to (mis)match against.
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(
        secret=_SECRET,
        request=_chat("claude-opus-4-99", temperature=0.5, top_p=0.9, effort="high"),
    )
    sent = json.loads(route.calls.last.request.content)
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "output_config" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_drops_effort_on_a_model_whose_spec_refuses_it() -> None:
    # `output_config.effort` 400s on Haiku 4.5, which ships in the catalogue, so
    # an agent configured on it with `effort` set failed every turn.
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(secret=_SECRET, request=_chat("claude-haiku-4-5", effort="low"))
    assert "output_config" not in json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
@respx.mock
async def test_provider_error_fields_that_are_not_identifier_shaped_are_dropped() -> None:
    # These fields are provider-controlled text. A sentence -- or a masked key
    # reflection smuggled into `code` -- is dropped whole rather than truncated.
    respx.post(_RESPONSES_URL).respond(
        400,
        json={"error": {"type": "invalid_request_error", "code": f"your key {_SECRET} is bad"}},
    )
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-5.4"))
    assert res.body == {"error": "HTTP 400 (type=invalid_request_error)"}
    assert _SECRET not in json.dumps(res.body)


# --------------------------------------------------------------------------- #
# OpenAI                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_translates_system_and_tools() -> None:
    route = respx.post(_RESPONSES_URL).respond(
        200,
        json=_response_obj(usage={"input_tokens": 3, "output_tokens": 4}),
    )
    req = _chat(
        "gpt-4o",
        system="be terse",
        tools=[{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
    )
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=req)
    assert res.body["text"] == "ok"
    assert res.body["finish_reason"] == "completed"
    assert res.input_tokens == 3
    assert res.output_tokens == 4
    sent = json.loads(route.calls.last.request.content)
    # The system prompt is a field here, not the first message.
    assert sent["instructions"] == "be terse"
    assert sent["input"] == [{"role": "user", "content": "hi"}]
    # Internal tagging: no `function` wrapper.
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["name"] == "f"
    assert "function" not in sent["tools"][0]
    # Agent-authored schemas are arbitrary JSON Schema; strict mode would
    # reject the ones that do not meet its structural requirements.
    assert sent["tools"][0]["strict"] is False


@pytest.mark.asyncio
@respx.mock
async def test_openai_never_lets_the_provider_retain_the_turn() -> None:
    # `store` is opt-OUT on this endpoint: omitting it leaves every turn's
    # content with OpenAI for at least 30 days, which the endpoint this adapter
    # migrated off never did. Unconditional, on both paths.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-4o"))
    assert json.loads(route.calls.last.request.content)["store"] is False

    respx.clear()
    stream_route = respx.post(_RESPONSES_URL).mock(
        return_value=_sse({"type": "response.completed", "response": _response_obj()})
    )
    async for _ in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o")):
        pass
    assert json.loads(stream_route.calls.last.request.content)["store"] is False


@pytest.mark.asyncio
@respx.mock
async def test_openai_asks_for_encrypted_reasoning_it_can_replay() -> None:
    # Pairs with the provider_items replay: without this, the reasoning items
    # come back empty and there is nothing to hand the model next round.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-4o"))
    assert json.loads(route.calls.last.request.content)["include"] == ["reasoning.encrypted_content"]


@pytest.mark.asyncio
@respx.mock
async def test_openai_replays_provider_items_instead_of_synthesising_them() -> None:
    # The reasoning item is opaque to every layer above the adapter and is the
    # only reason this path exists: dropping it costs a reasoning model its
    # chain across a tool round, which no assertion elsewhere could see.
    reasoning = {"type": "reasoning", "id": "rs_1", "encrypted_content": "OPAQUE", "summary": []}
    items = [reasoning, _call_item("c1", "f", '{"x": 1}')]
    route = _ok_chat_route()
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={
            "model": "gpt-5.4",
            "messages": [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "c1", "name": "f", "arguments": {"x": 1}}],
                    "provider_items": items,
                    "provider_items_key": _key_tag(_SECRET),
                },
                {"role": "tool", "tool_call_id": "c1", "name": "f", "content": "42"},
            ],
        },
    )
    await OpenAIAdapter().invoke(secret=_SECRET, request=req)
    sent = json.loads(route.calls.last.request.content)["input"]
    assert sent[1] == reasoning
    assert sent[2] == items[1]
    assert sent[3] == {"type": "function_call_output", "call_id": "c1", "output": "42"}


def _replay_request(items: list[dict], key_tag: object) -> ProviderRequest:
    return ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={
            "model": "gpt-5.4",
            "messages": [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "c1", "name": "f", "arguments": {}}],
                    "provider_items": items,
                    "provider_items_key": key_tag,
                },
            ],
        },
    )


@pytest.mark.asyncio
@respx.mock
async def test_openai_does_not_replay_items_produced_by_a_different_key() -> None:
    # Each tool round is an independent `call_stream` that re-picks a group
    # member, and a key group legitimately holds keys from more than one OpenAI
    # account. Encrypted reasoning is decryptable only by the account that
    # produced it, so replaying it to a different key is a deterministic 400 --
    # which `classify_http` turns into ABORT, killing the whole group instead of
    # rotating. Falling back to synthesised items loses the chain and keeps the
    # turn, which is the trade this check exists to make.
    items = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "OPAQUE"}]
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_replay_request(items, "a-different-key"))
    sent = json.loads(route.calls.last.request.content)["input"]
    assert not any(item.get("type") == "reasoning" for item in sent)
    assert sent[1] == {"type": "function_call", "call_id": "c1", "name": "f", "arguments": "{}"}


@pytest.mark.asyncio
@respx.mock
async def test_openai_replays_items_when_the_key_still_matches() -> None:
    items = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "OPAQUE"}]
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_replay_request(items, _key_tag(_SECRET)))
    assert json.loads(route.calls.last.request.content)["input"][1] == items[0]


@pytest.mark.asyncio
@respx.mock
async def test_the_key_tag_is_never_the_key_and_never_leaves_the_process() -> None:
    # It is a truncated digest, and it is read by `_input_items` rather than
    # sent: nothing about the secret may reach the provider outside the header.
    items = [{"type": "reasoning", "id": "rs_1"}]
    route = _ok_chat_route()
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-5.4"))
    tag = res.body["provider_items_key"]
    assert tag == _key_tag(_SECRET)
    assert _SECRET not in tag
    assert tag not in _SECRET
    respx.clear()
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_replay_request(items, tag))
    body = route.calls.last.request.content.decode()
    assert "provider_items_key" not in body
    assert tag not in body
    assert _SECRET not in body


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(("code", "status"), [("rate_limit_exceeded", 429), ("server_error", 500)])
async def test_openai_non_streaming_failed_status_is_not_an_empty_success(code: str, status: int) -> None:
    # A run that fails after the request was accepted returns HTTP 200 with
    # `status: "failed"` and an empty `output`. Chat Completions had no such
    # shape. Left unmapped the router books a success, never rotates, and the
    # caller reads the empty string as the model's answer -- an empty summary
    # or zero extracted triples, silently.
    respx.post(_RESPONSES_URL).respond(
        200,
        json=_response_obj(
            status="failed",
            output=[],
            error={"code": code, "message": f"key {_SECRET} oops"},
            usage={"input_tokens": 6, "output_tokens": 0},
        ),
    )
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-5.4"))
    assert res.http_status == status
    assert res.body == {"error": f"HTTP {status} ({code})"}
    assert res.input_tokens == 6  # still billed
    assert _SECRET not in json.dumps(res.body)


@pytest.mark.asyncio
@respx.mock
async def test_openai_returns_its_output_items_for_replay() -> None:
    items = [_text_item("ok"), _call_item("c1", "f", '{"x": 1}')]
    respx.post(_RESPONSES_URL).respond(
        200, json=_response_obj(output=items, usage={"input_tokens": 7, "output_tokens": 3})
    )
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-5.4"))
    assert res.body["provider_items"] == items
    # The non-streaming half of the call_id rule: `id` here is `fc_c1`, and
    # taking it instead of `call_id` breaks the next round's pairing.
    assert res.body["tool_calls"] == [{"id": "c1", "name": "f", "arguments": {"x": 1}}]
    assert (res.input_tokens, res.output_tokens) == (7, 3)


@pytest.mark.asyncio
@respx.mock
async def test_openai_embedding_preserves_order() -> None:
    respx.post("https://api.openai.com/v1/embeddings").respond(
        200,
        json={
            "data": [
                {"index": 1, "embedding": [0.2]},
                {"index": 0, "embedding": [0.1]},
            ],
            "usage": {"prompt_tokens": 9},
        },
    )
    req = ProviderRequest(
        capability=ProviderCapability.EMBEDDING,
        payload={"model": "text-embedding-3-small", "input": ["a", "b"]},
    )
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=req)
    assert res.body["embeddings"] == [[0.1], [0.2]]  # re-sorted by index
    assert res.input_tokens == 9


@pytest.mark.asyncio
@respx.mock
async def test_openai_stream_reassembles() -> None:
    # No `[DONE]` sentinel on this endpoint: `response.completed` is terminal
    # and carries the whole response object, usage included.
    respx.post(_RESPONSES_URL).mock(
        return_value=_sse(
            {"type": "response.created", "response": {"id": "resp_1", "status": "in_progress"}},
            {"type": "response.output_text.delta", "output_index": 0, "delta": "Hel"},
            {"type": "response.output_text.delta", "output_index": 0, "delta": "lo"},
            {
                "type": "response.completed",
                "response": _response_obj(
                    output=[_text_item("Hello")],
                    usage={"input_tokens": 5, "output_tokens": 2},
                ),
            },
        )
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    assert "".join(e.text for e in events if isinstance(e, TokenDelta)) == "Hello"
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert len([e for e in events if isinstance(e, StreamComplete)]) == 1
    assert final.result.body["text"] == "Hello"
    assert final.result.output_tokens == 2
    assert final.result.input_tokens == 5


@pytest.mark.asyncio
@respx.mock
async def test_openai_stream_takes_tool_calls_whole_from_the_terminal_response() -> None:
    # Argument fragments stream as `response.function_call_arguments.delta`, but
    # the terminal response object carries each item complete -- so the adapter
    # reads them there rather than reassembling, and a fragment that never
    # arrived cannot corrupt the result.
    respx.post(_RESPONSES_URL).mock(
        return_value=_sse(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "function_call", "call_id": "c1", "name": "f", "arguments": ""},
            },
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": '{"x"'},
            {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": ": 1}"},
            {
                "type": "response.completed",
                "response": _response_obj(
                    output=[_call_item("c1", "f", '{"x": 1}'), _call_item("c2", "g", "{}")]
                ),
            },
        )
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    # `call_id`, not the item's own `id` -- this is the value the next round's
    # function_call_output has to echo.
    assert final.result.body["tool_calls"] == [
        {"id": "c1", "name": "f", "arguments": {"x": 1}},
        {"id": "c2", "name": "g", "arguments": {}},
    ]
    assert final.result.body["finish_reason"] == "completed"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("rate_limit_exceeded", 429),
        ("server_error", 500),
        # Faults the request, so every sibling key refuses it identically: 400
        # aborts the group instead of replaying it against each member.
        ("invalid_prompt", 400),
        ("image_content_policy_violation", 400),
        # Faults the account or the moment, not the request -- another key may
        # succeed, so these rotate.
        ("data_residency_mismatch", 500),
        ("vector_store_timeout", 500),
        ("a_code_this_table_has_never_seen", 500),
    ],
)
async def test_openai_in_stream_error_event_maps_to_non_2xx(kind: str, status: int) -> None:
    # A top-level `error` event arrives inside an HTTP 200 and must NOT pass as
    # success: the router's rotate-versus-abort decision reads this status.
    respx.post(_RESPONSES_URL).mock(
        return_value=_sse({"type": "error", "code": kind, "message": f"key {_SECRET} oops"})
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    assert len(events) == 1
    final = events[0]
    assert isinstance(final, StreamComplete)
    assert final.result.http_status == status
    assert final.result.body == {"error": f"HTTP {status} ({kind})"}
    assert _SECRET not in json.dumps(final.result.body)  # scrubbed


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("kind", "status"),
    [("rate_limit_exceeded", 429), ("server_error", 500)],
)
async def test_openai_response_failed_maps_to_non_2xx(kind: str, status: int) -> None:
    # The second in-stream failure shape. `response.failed` carries its code on
    # `response.error`, a narrower object than the HTTP error envelope.
    respx.post(_RESPONSES_URL).mock(
        return_value=_sse(
            {
                "type": "response.failed",
                "response": _response_obj(
                    status="failed",
                    error={"code": kind, "message": f"key {_SECRET} oops"},
                    usage={"input_tokens": 4, "output_tokens": 1},
                ),
            }
        )
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.http_status == status
    assert final.result.body == {"error": f"HTTP {status} ({kind})"}
    assert final.result.input_tokens == 4  # still billed
    assert _SECRET not in json.dumps(final.result.body)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "code",
    [
        f"your key {_SECRET} is bad",
        "rate limit exceeded, please retry",
        "x" * 65,
        {"nested": "object"},
    ],
)
async def test_in_stream_error_code_that_is_not_identifier_shaped_is_dropped(code: object) -> None:
    # An error delivered inside an HTTP 200 is no more trustworthy than one that
    # arrived with a status code, so it goes through the same `safe_ident`
    # filter the non-2xx path applies. A sentence -- or a masked key reflection
    # smuggled into `code` -- is dropped whole rather than truncated.
    respx.post(_RESPONSES_URL).mock(return_value=_sse({"type": "error", "code": code, "message": "boom"}))
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.body == {"error": "HTTP 500"}
    assert _SECRET not in json.dumps(final.result.body)


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_in_stream_error_type_is_filtered_the_same_way() -> None:
    # The filter lives in `base.scrub_stream_error`, so all three adapters get
    # it at once -- this pins that it reaches the Anthropic path too.
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=_sse({"type": "error", "error": {"type": f"key {_SECRET} rejected"}})
    )
    events = [e async for e in AnthropicAdapter().stream(secret=_SECRET, request=_chat("claude-x"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.body == {"error": "HTTP 500"}
    assert _SECRET not in json.dumps(final.result.body)


@pytest.mark.asyncio
@respx.mock
async def test_openai_stream_cut_before_a_terminal_event_is_a_failure() -> None:
    # There is no `[DONE]` sentinel to distinguish "finished" from "cut", so a
    # stream that simply stops must not be reported as a successful empty turn.
    respx.post(_RESPONSES_URL).mock(
        return_value=_sse({"type": "response.output_text.delta", "output_index": 0, "delta": "Hi"})
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.http_status == 500


# --------------------------------------------------------------------------- #
# Gemini — key MUST be a header, never the query string                        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_gemini_chat_uses_header_not_query_key() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    ).respond(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
        },
    )
    res = await GeminiAdapter().invoke(secret=_SECRET, request=_chat("gemini-2.0-flash"))
    assert res.body["text"] == "hi"
    assert res.input_tokens == 2
    assert res.output_tokens == 1
    req = route.calls.last.request
    assert req.headers["x-goog-api-key"] == _SECRET
    assert _SECRET not in str(req.url)  # never on the query string


@pytest.mark.asyncio
@respx.mock
async def test_gemini_percent_encodes_the_model_id_in_the_url_path() -> None:
    # model_id is caller-supplied; a value containing a path separator or a
    # query/fragment delimiter must not be able to steer the request to a
    # different path or query on this host (FU-14 fix). respx matches the
    # ACTUAL bytes on the wire, so registering the route at the percent-encoded
    # path and asserting a 200 (rather than respx's "no matching route" error)
    # is the proof the '/' characters never reached the wire as path separators.
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/evil%2F..%2Fadmin:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    res = await GeminiAdapter().invoke(secret=_SECRET, request=_chat("evil/../admin"))
    assert res.http_status == 200


@pytest.mark.asyncio
@respx.mock
async def test_gemini_stream_header_and_reassembly() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:streamGenerateContent"
    ).mock(
        return_value=_sse(
            {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
            {
                "candidates": [{"content": {"parts": [{"text": "lo"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2},
            },
        )
    )
    events = [e async for e in GeminiAdapter().stream(secret=_SECRET, request=_chat("gemini-x"))]
    assert "".join(e.text for e in events if isinstance(e, TokenDelta)) == "Hello"
    assert events[-1].result.output_tokens == 2
    assert route.calls.last.request.headers["x-goog-api-key"] == _SECRET
    assert _SECRET not in str(route.calls.last.request.url)
    # Only `alt=sse` rides the query string (the key never does).
    assert route.calls.last.request.url.params["alt"] == "sse"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("code", "kind", "status"),
    [(429, "RESOURCE_EXHAUSTED", 429), ("oops", "INTERNAL", 500)],
)
async def test_gemini_in_stream_error_maps_to_non_2xx(code, kind: str, status: int) -> None:
    # In-band `{"error": {...}}` objects (no candidates) must NOT pass as success.
    respx.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-x:streamGenerateContent").mock(
        return_value=_sse(
            {"error": {"code": code, "status": kind, "message": f"key {_SECRET} oops"}},
        )
    )
    events = [e async for e in GeminiAdapter().stream(secret=_SECRET, request=_chat("gemini-x"))]
    assert len(events) == 1
    final = events[0]
    assert isinstance(final, StreamComplete)
    assert final.result.http_status == status
    assert final.result.body == {"error": f"HTTP {status} ({kind})"}
    assert _SECRET not in json.dumps(final.result.body)  # scrubbed


@pytest.mark.asyncio
@respx.mock
async def test_gemini_stream_block_reason_surfaces_as_finish_reason() -> None:
    # Prompt blocked: zero candidates is NOT an empty success.
    respx.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-x:streamGenerateContent").mock(
        return_value=_sse({"promptFeedback": {"blockReason": "SAFETY"}})
    )
    events = [e async for e in GeminiAdapter().stream(secret=_SECRET, request=_chat("gemini-x"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.http_status == 200
    assert final.result.body["text"] == ""
    assert final.result.body["finish_reason"] == "blocked:SAFETY"


# --------------------------------------------------------------------------- #
# Voyage (embed) + Cohere (rerank)                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_voyage_embedding() -> None:
    respx.post("https://api.voyageai.com/v1/embeddings").respond(
        200,
        json={"data": [{"index": 0, "embedding": [0.5, 0.6]}], "usage": {"total_tokens": 4}},
    )
    req = ProviderRequest(
        capability=ProviderCapability.EMBEDDING,
        payload={"model": "voyage-3", "input": ["x"]},
    )
    res = await VoyageAdapter().invoke(secret=_SECRET, request=req)
    assert res.body["embeddings"] == [[0.5, 0.6]]
    assert res.input_tokens == 4


@pytest.mark.asyncio
@respx.mock
async def test_cohere_rerank() -> None:
    respx.post("https://api.cohere.com/v1/rerank").respond(
        200,
        json={"results": [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.4}]},
    )
    req = ProviderRequest(
        capability=ProviderCapability.RERANK,
        payload={"model": "rerank-3", "query": "q", "documents": ["a", "b", "c"], "top_n": 2},
    )
    res = await CohereAdapter().invoke(secret=_SECRET, request=req)
    assert res.body["results"] == [
        {"index": 2, "relevance_score": 0.9},
        {"index": 0, "relevance_score": 0.4},
    ]


def _tool_roundtrip_messages() -> list[dict]:
    return [
        {"role": "user", "content": "set cadence"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "t1", "name": "f", "arguments": {"x": 1}}],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "f", "content": "42"},
    ]


@pytest.mark.asyncio
@respx.mock
async def test_openai_translates_tool_roundtrip() -> None:
    route = _ok_chat_route()
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={"model": "gpt-4o", "messages": _tool_roundtrip_messages()},
    )
    await OpenAIAdapter().invoke(secret=_SECRET, request=req)
    items = json.loads(route.calls.last.request.content)["input"]
    # One neutral assistant message expands into items; the empty text does not
    # become an empty message item.
    assert items == [
        {"role": "user", "content": "set cadence"},
        {"type": "function_call", "call_id": "t1", "name": "f", "arguments": json.dumps({"x": 1})},
        {"type": "function_call_output", "call_id": "t1", "output": "42"},
    ]


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_translates_tool_roundtrip() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={"model": "claude-x", "messages": _tool_roundtrip_messages()},
    )
    await AnthropicAdapter().invoke(secret=_SECRET, request=req)
    msgs = json.loads(route.calls.last.request.content)["messages"]
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[1]["content"][0]["input"] == {"x": 1}
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "t1"


@pytest.mark.asyncio
@respx.mock
async def test_gemini_translates_tool_roundtrip() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={"model": "gemini-x", "messages": _tool_roundtrip_messages()},
    )
    await GeminiAdapter().invoke(secret=_SECRET, request=req)
    contents = json.loads(route.calls.last.request.content)["contents"]
    assert contents[1]["parts"][0]["functionCall"]["name"] == "f"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "f"
    assert contents[2]["parts"][0]["functionResponse"]["response"] == {"result": "42"}


@pytest.mark.asyncio
async def test_adapter_rejects_wrong_capability() -> None:
    with pytest.raises(ValueError):
        await VoyageAdapter().invoke(secret=_SECRET, request=_chat("voyage-3"))
    with pytest.raises(ValueError):
        await AnthropicAdapter().invoke(
            secret=_SECRET,
            request=ProviderRequest(
                capability=ProviderCapability.EMBEDDING,
                payload={"model": "x", "input": ["y"]},
            ),
        )


@pytest.mark.asyncio
async def test_resolve_model_required() -> None:
    # No model and no models-map → the adapter refuses rather than guessing.
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={"messages": [{"role": "user", "content": "hi"}]},
    )
    with pytest.raises(ValueError):
        await AnthropicAdapter().invoke(secret=_SECRET, request=req)


# --------------------------------------------------------------------------- #
# Cross-provider effort -> per-provider reasoning-effort parameter             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_anthropic_maps_effort_to_output_config() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(
        secret=_SECRET,
        request=_chat("claude-opus-4-8", effort="high", effort_values=("low", "medium", "high")),
    )
    body = json.loads(route.calls.last.request.content)
    assert body["output_config"] == {"effort": "high"}


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_drops_effort_on_a_conflicting_model_even_though_the_value_is_listed() -> None:
    # No current Claude row sets effort_conflicts_with_tools, but the payload
    # flag is attached uniformly across all three providers (R9.03a) -- a
    # future row that does must not have this adapter silently ignore it, the
    # way openai.py already guards for gpt-5.4+.
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(
        secret=_SECRET,
        request=_chat(
            "claude-opus-4-8",
            effort="high",
            effort_values=("low", "medium", "high"),
            effort_conflicts_with_tools=True,
        ),
    )
    assert "output_config" not in json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
@respx.mock
async def test_openai_maps_effort_to_reasoning_effort() -> None:
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET,
        request=_chat(
            "gpt-5.4",
            effort="medium",
            effort_values=("low", "medium", "high"),
            effort_conflicts_with_tools=False,
        ),
    )
    assert json.loads(route.calls.last.request.content)["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
@respx.mock
async def test_gemini_maps_effort_to_thinking_level_uppercase() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    await GeminiAdapter().invoke(
        secret=_SECRET, request=_chat("gemini-x", effort="low", effort_values=("low", "medium", "high"))
    )
    gen = json.loads(route.calls.last.request.content)["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"}


@pytest.mark.asyncio
@respx.mock
async def test_gemini_drops_thinking_config_on_a_conflicting_model_even_though_the_value_is_listed() -> None:
    # No current Gemini row sets effort_conflicts_with_tools, but the payload
    # flag is attached uniformly across all three providers (R9.03a) -- a
    # future row that does must not have this adapter silently ignore it, the
    # way openai.py already guards for gpt-5.4+.
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    await GeminiAdapter().invoke(
        secret=_SECRET,
        request=_chat(
            "gemini-x",
            effort="low",
            effort_values=("low", "medium", "high"),
            effort_conflicts_with_tools=True,
        ),
    )
    sent = json.loads(route.calls.last.request.content)
    assert "thinkingConfig" not in sent.get("generationConfig", {})


@pytest.mark.asyncio
@respx.mock
async def test_gemini_drops_thinking_config_for_a_model_whose_spec_refuses_effort() -> None:
    # AC-5: gemini.py never gated `thinkingConfig` at all before this table --
    # any effort setting reached any model. An unsupported model rejects the
    # field with a deterministic 400, which aborts the whole key group.
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    await GeminiAdapter().invoke(secret=_SECRET, request=_chat("gemini-x", effort="low"))
    sent = json.loads(route.calls.last.request.content)
    assert "thinkingConfig" not in sent.get("generationConfig", {})


@pytest.mark.asyncio
@respx.mock
async def test_effort_omitted_when_unset() -> None:
    # Opt-in: with no effort in the payload the parameter is never sent, so the
    # provider's own default applies (and non-reasoning models don't 400).
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-4o"))
    assert "reasoning" not in json.loads(route.calls.last.request.content)


@pytest.mark.asyncio
@respx.mock
async def test_openai_output_ceiling_has_one_name_on_every_model() -> None:
    # The Chat Completions split between `max_tokens` and
    # `max_completion_tokens` -- which reasoning models 400 on -- does not exist
    # here, so `uses_completion_token_field` changes nothing either way.
    for flag in (True, False):
        respx.clear()
        route = _ok_chat_route()
        await OpenAIAdapter().invoke(
            secret=_SECRET,
            request=_chat(
                "o3-mini",
                effort="high",
                max_tokens=256,
                temperature=0.7,
                effort_values=("low", "medium", "high"),
                uses_completion_token_field=flag,
            ),
        )
        sent = json.loads(route.calls.last.request.content)
        assert sent["reasoning"] == {"effort": "high"}
        assert sent["max_output_tokens"] == 256
        assert "max_tokens" not in sent
        assert "max_completion_tokens" not in sent
        assert "temperature" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_openai_default_chat_model_is_a_reasoning_model_and_drops_temperature() -> None:
    """The composition ``docs/examples/creative-thinking-course.md`` now cites.

    An agent with no ``model_id`` resolves to ``DEFAULT_CHAT_MODELS["openai"]``,
    so an agent pack installed against an OpenAI-only key group runs on whatever
    that is — and that model's capability-table row (`accepts_sampling`) is what
    silently voids the pack's shipped ``temperature``. Both halves are pinned
    here, and deliberately across the context boundary: neither side alone can
    state the consequence, and the document promises it.
    """
    from contexts.agents.domain.model_specs import capability_fields, resolve_spec
    from contexts.agents.domain.models import DEFAULT_CHAT_MODELS

    default = DEFAULT_CHAT_MODELS["openai"]
    spec = resolve_spec("openai", default)
    assert spec.accepts_sampling is False

    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET, request=_chat(default, temperature=0.2, top_p=0.9, **capability_fields(spec))
    )
    sent = json.loads(route.calls.last.request.content)
    assert "temperature" not in sent
    assert "top_p" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_openai_non_reasoning_model_drops_effort_keeps_sampling() -> None:
    # Setting effort on a model whose spec lists no effort values must not 400:
    # `reasoning` is dropped while the sampling controls are preserved.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET,
        request=_chat("gpt-4o", effort="high", max_tokens=256, temperature=0.7, accepts_sampling=True),
    )
    sent = json.loads(route.calls.last.request.content)
    assert "reasoning" not in sent
    assert sent["max_output_tokens"] == 256
    assert sent["temperature"] == 0.7


# --------------------------------------------------------------------------- #
# Sampling controls — temperature / top_p / seed (R9.18)                        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-opus-4-5", "claude-haiku-4-5"])
async def test_anthropic_forwards_temperature_and_top_p_on_accepting_models(model: str) -> None:
    # Older Claude generations still accept sampling params; seed has no
    # Anthropic equivalent and must never be forwarded.
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(
        secret=_SECRET, request=_chat(model, temperature=0.0, top_p=1.0, seed=7, accepts_sampling=True)
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["temperature"] == 0.0
    assert sent["top_p"] == 1.0
    assert "seed" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_clamps_temperature_to_provider_ceiling() -> None:
    # The agent field allows temperature up to 2.0 (OpenAI/Gemini's range), but
    # Anthropic caps at 1.0; a cross-provider value must clamp, not 400.
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(
        secret=_SECRET, request=_chat("claude-sonnet-4-6", temperature=1.5, accepts_sampling=True)
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["temperature"] == 1.0


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "model", ["claude-opus-4-7", "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5", "claude-mythos-5"]
)
async def test_anthropic_drops_sampling_on_rejecting_models(model: str) -> None:
    # Opus 4.7+ and every "5"-generation model 400 on temperature/top_p; the
    # adapter drops them so a configured value degrades to the provider default.
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        200, json={"content": [{"type": "text", "text": "ok"}], "stop_reason": "end", "usage": {}}
    )
    await AnthropicAdapter().invoke(secret=_SECRET, request=_chat(model, temperature=0.0, top_p=1.0, seed=7))
    sent = json.loads(route.calls.last.request.content)
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "seed" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_openai_forwards_top_p_and_never_seed() -> None:
    # `seed` has no Responses API equivalent. It stays a live agent field
    # (agents.seed) with no OpenAI destination, so it must be dropped even on a
    # model that accepts the other two, or the request 400s on an unknown
    # parameter.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET, request=_chat("gpt-4o", temperature=0.0, top_p=1.0, seed=42, accepts_sampling=True)
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent["temperature"] == 0.0
    assert sent["top_p"] == 1.0
    assert "seed" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_openai_reasoning_drops_all_sampling_controls() -> None:
    # Reasoning models reject sampling controls; temperature, top_p, and seed are
    # all dropped so a configured value degrades to the default instead of 400ing.
    route = _ok_chat_route()
    await OpenAIAdapter().invoke(
        secret=_SECRET, request=_chat("o3-mini", temperature=0.0, top_p=1.0, seed=42)
    )
    sent = json.loads(route.calls.last.request.content)
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "seed" not in sent


@pytest.mark.asyncio
@respx.mock
async def test_gemini_forwards_top_p_and_ignores_seed() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    await GeminiAdapter().invoke(
        secret=_SECRET, request=_chat("gemini-x", temperature=0.0, top_p=0.9, seed=7, accepts_sampling=True)
    )
    gen = json.loads(route.calls.last.request.content)["generationConfig"]
    assert gen["temperature"] == 0.0
    assert gen["topP"] == 0.9
    assert "seed" not in gen
