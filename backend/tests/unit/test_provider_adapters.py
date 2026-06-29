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
)
from contexts.keys.domain.providers import ProviderCapability
from contexts.keys.infrastructure.adapters.anthropic import AnthropicAdapter
from contexts.keys.infrastructure.adapters.cohere import CohereAdapter
from contexts.keys.infrastructure.adapters.gemini import GeminiAdapter
from contexts.keys.infrastructure.adapters.openai import OpenAIAdapter
from contexts.keys.infrastructure.adapters.voyage import VoyageAdapter

_SECRET = "sk-super-secret-key-value"


def _chat(model: str, **extra: object) -> ProviderRequest:
    payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}
    return ProviderRequest(capability=ProviderCapability.LLM_CHAT, payload=payload)


def _sse(*objs: dict, done: bool = False) -> httpx.Response:
    body = "".join(f"data: {json.dumps(o)}\n\n" for o in objs)
    if done:
        body += "data: [DONE]\n\n"  # OpenAI terminal sentinel
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


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
    assert res.body == {"error": "HTTP 400 (invalid_request_error)"}
    assert _SECRET not in json.dumps(res.body)  # no key reflection


# --------------------------------------------------------------------------- #
# OpenAI                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@respx.mock
async def test_openai_chat_translates_system_and_tools() -> None:
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200,
        json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
    )
    req = _chat(
        "gpt-4o",
        system="be terse",
        tools=[{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
    )
    res = await OpenAIAdapter().invoke(secret=_SECRET, request=req)
    assert res.body["text"] == "ok"
    assert res.input_tokens == 3
    assert res.output_tokens == 4
    sent = json.loads(route.calls.last.request.content)
    assert sent["messages"][0] == {"role": "system", "content": "be terse"}
    assert sent["tools"][0]["type"] == "function"
    assert sent["tools"][0]["function"]["name"] == "f"


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
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=_sse(
            {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
            done=True,  # OpenAI closes with `data: [DONE]`
        )
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    assert "".join(e.text for e in events if isinstance(e, TokenDelta)) == "Hello"
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.output_tokens == 2
    assert final.result.input_tokens == 5
    # The outbound request must opt into usage frames on the final chunk.
    sent = json.loads(route.calls.last.request.content)
    assert sent["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
@respx.mock
async def test_openai_stream_merges_tool_call_deltas_by_index() -> None:
    # Tool calls stream as index-keyed fragments: id/name first, args appended.
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=_sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "c1", "function": {"name": "f", "arguments": '{"x"'}}
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": ": 1}"}},
                                {"index": 1, "id": "c2", "function": {"name": "g", "arguments": "{}"}},
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            done=True,
        )
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    final = events[-1]
    assert isinstance(final, StreamComplete)
    assert final.result.body["tool_calls"] == [
        {"id": "c1", "name": "f", "arguments": {"x": 1}},
        {"id": "c2", "name": "g", "arguments": {}},
    ]
    assert final.result.body["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("kind", "status"),
    [("rate_limit_exceeded", 429), ("server_error", 500)],
)
async def test_openai_in_stream_error_maps_to_non_2xx(kind: str, status: int) -> None:
    # `data: {"error": {...}}` chunks (no choices) must NOT pass as success.
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=_sse(
            {"error": {"type": kind, "message": f"key {_SECRET} oops"}},
        )
    )
    events = [e async for e in OpenAIAdapter().stream(secret=_SECRET, request=_chat("gpt-4o"))]
    assert len(events) == 1
    final = events[0]
    assert isinstance(final, StreamComplete)
    assert final.result.http_status == status
    assert final.result.body == {"error": f"HTTP {status} ({kind})"}
    assert _SECRET not in json.dumps(final.result.body)  # scrubbed


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
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
    )
    req = ProviderRequest(
        capability=ProviderCapability.LLM_CHAT,
        payload={"model": "gpt-4o", "messages": _tool_roundtrip_messages()},
    )
    await OpenAIAdapter().invoke(secret=_SECRET, request=req)
    msgs = json.loads(route.calls.last.request.content)["messages"]
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == json.dumps({"x": 1})
    assert msgs[2] == {"role": "tool", "tool_call_id": "t1", "content": "42"}


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
    await AnthropicAdapter().invoke(secret=_SECRET, request=_chat("claude-x", effort="high"))
    body = json.loads(route.calls.last.request.content)
    assert body["output_config"] == {"effort": "high"}


@pytest.mark.asyncio
@respx.mock
async def test_openai_maps_effort_to_reasoning_effort() -> None:
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
    )
    await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-5.4", effort="medium"))
    assert json.loads(route.calls.last.request.content)["reasoning_effort"] == "medium"


@pytest.mark.asyncio
@respx.mock
async def test_gemini_maps_effort_to_thinking_level_uppercase() -> None:
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent"
    ).respond(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    await GeminiAdapter().invoke(secret=_SECRET, request=_chat("gemini-x", effort="low"))
    gen = json.loads(route.calls.last.request.content)["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"}


@pytest.mark.asyncio
@respx.mock
async def test_effort_omitted_when_unset() -> None:
    # Opt-in: with no effort in the payload the parameter is never sent, so the
    # provider's own default applies (and non-reasoning models don't 400).
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
    )
    await OpenAIAdapter().invoke(secret=_SECRET, request=_chat("gpt-4o"))
    assert "reasoning_effort" not in json.loads(route.calls.last.request.content)
