"""OpenAI adapter — Chat Completions (streaming, tools) + Embeddings (K.1).

One adapter, two capabilities (LLM_CHAT, EMBEDDING) dispatched on
``request.capability`` — the router maps exactly one adapter per provider.
Auth via ``Authorization: Bearer``; model id is always caller-supplied.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from contexts.keys.application.provider_router import (
    ProviderCallResult,
    ProviderRequest,
    StreamComplete,
    StreamEvent,
    TokenDelta,
)
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability
from contexts.keys.infrastructure.adapters import base

_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_EMBED_URL = "https://api.openai.com/v1/embeddings"


def _headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def _messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    if payload.get("system"):
        # OpenAI carries the system prompt as the first message, not a field.
        msgs.append({"role": "system", "content": payload["system"]})
    for m in payload["messages"]:
        role = m.get("role")
        if role == "tool":
            msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id"),
                    "content": m.get("content", ""),
                }
            )
        elif role == "assistant" and m.get("tool_calls"):
            msgs.append(
                {
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name"),
                                "arguments": json.dumps(tc.get("arguments") or {}),
                            },
                        }
                        for tc in m["tool_calls"]
                    ],
                }
            )
        else:
            msgs.append(
                {"role": role if role in ("user", "assistant") else "user", "content": m.get("content", "")}
            )
    return msgs


def _tools(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    tools = payload.get("tools")
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


def _chat_body(request: ProviderRequest, *, stream: bool) -> dict[str, Any]:
    payload = request.payload
    body: dict[str, Any] = {
        "model": base.resolve_model(payload, ApiKeyProvider.OPENAI),
        "messages": _messages(payload),
    }
    if payload.get("max_tokens") is not None:
        body["max_tokens"] = payload["max_tokens"]
    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    tools = _tools(payload)
    if tools:
        body["tools"] = tools
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


def _normalise_message(message: dict[str, Any], finish_reason: str | None) -> dict[str, Any]:
    tool_calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        tool_calls.append(
            {
                "id": tc.get("id"),
                "name": fn.get("name"),
                "arguments": _safe_json(fn.get("arguments", "")),
            }
        )
    return {
        "text": message.get("content") or "",
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
    }


class OpenAIAdapter:
    provider = ApiKeyProvider.OPENAI

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        if request.capability is ProviderCapability.EMBEDDING:
            return await self._embed(secret=secret, request=request)
        if request.capability is ProviderCapability.LLM_CHAT:
            return await self._chat(secret=secret, request=request)
        raise ValueError(f"openai does not serve {request.capability.value}")

    async def _chat(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        async with base.new_client() as client:
            resp = await client.post(
                _CHAT_URL, json=_chat_body(request, stream=False), headers=_headers(secret)
            )
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return ProviderCallResult(
            http_status=200,
            body=_normalise_message(choice.get("message") or {}, choice.get("finish_reason")),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )

    async def _embed(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        payload = request.payload
        body = {
            "model": base.resolve_model(payload, ApiKeyProvider.OPENAI),
            "input": payload["input"],
        }
        async with base.new_client() as client:
            resp = await client.post(_EMBED_URL, json=body, headers=_headers(secret))
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        rows = sorted(data.get("data") or [], key=lambda d: d.get("index", 0))
        usage = data.get("usage") or {}
        return ProviderCallResult(
            http_status=200,
            body={"embeddings": [r["embedding"] for r in rows]},
            input_tokens=int(usage.get("prompt_tokens", 0)),
        )

    async def stream(self, *, secret: str, request: ProviderRequest) -> AsyncGenerator[StreamEvent, None]:
        if request.capability is not ProviderCapability.LLM_CHAT:
            raise ValueError(f"openai does not stream {request.capability.value}")
        text_parts: list[str] = []
        tool_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        input_tokens = 0
        output_tokens = 0

        async with base.new_client(stream=True) as client:  # noqa: SIM117
            async with client.stream(
                "POST", _CHAT_URL, json=_chat_body(request, stream=True), headers=_headers(secret)
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    yield StreamComplete(
                        ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
                    )
                    return
                async for data in base.iter_sse_lines(resp):
                    ev = json.loads(data)
                    err = ev.get("error")
                    if isinstance(err, dict) and not ev.get("choices"):
                        # Mid-stream failure delivered inside an HTTP 200 —
                        # map onto a synthetic non-2xx for router classification.
                        kind = err.get("type") or err.get("code")
                        status = 429 if "rate_limit" in str(kind or "") else 500
                        yield StreamComplete(
                            ProviderCallResult(
                                http_status=status,
                                body=base.scrub_stream_error(status, kind),
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
                        )
                        return
                    usage = ev.get("usage")
                    if usage:
                        input_tokens = int(usage.get("prompt_tokens", input_tokens))
                        output_tokens = int(usage.get("completion_tokens", output_tokens))
                    for choice in ev.get("choices") or []:
                        delta = choice.get("delta") or {}
                        chunk = delta.get("content")
                        if chunk:
                            text_parts.append(chunk)
                            yield TokenDelta(chunk)
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            acc = tool_acc.setdefault(idx, {"id": None, "name": None, "args": ""})
                            if tc.get("id"):
                                acc["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                acc["name"] = fn["name"]
                            if fn.get("arguments"):
                                acc["args"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]

        tool_calls = [
            {"id": acc["id"], "name": acc["name"], "arguments": _safe_json(acc["args"])}
            for acc in tool_acc.values()
        ]
        yield StreamComplete(
            ProviderCallResult(
                http_status=200,
                body={
                    "text": "".join(text_parts),
                    "tool_calls": tool_calls,
                    "finish_reason": finish_reason,
                },
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )


def _safe_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["OpenAIAdapter"]
