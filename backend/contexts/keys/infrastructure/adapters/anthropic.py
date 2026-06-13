"""Anthropic (`claude`) adapter — Messages API, native SSE streaming (K.1).

LLM_CHAT only. Auth via the ``x-api-key`` header; the model id is always
caller-supplied (the agent's configured model), never hardcoded.
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

_URL = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"

# Anthropic delivers mid-stream failures as HTTP 200 + `{"type": "error", ...}`
# then closes the stream. Map the documented error types onto the HTTP status
# they would carry out-of-stream so the router's classify_http applies.
_STREAM_ERROR_STATUS: dict[str, int] = {
    "overloaded_error": 529,
    "rate_limit_error": 429,
}


def _headers(secret: str) -> dict[str, str]:
    return {
        "x-api-key": secret,
        "anthropic-version": _VERSION,
        "content-type": "application/json",
    }


def _translate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Neutral messages → Anthropic shape (tool_use / tool_result round-trip)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id"),
                            "content": m.get("content", ""),
                            "is_error": bool(m.get("is_error", False)),
                        }
                    ],
                }
            )
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id"),
                        "name": tc.get("name"),
                        "input": tc.get("arguments") or {},
                    }
                )
            out.append({"role": "assistant", "content": blocks})
            continue
        content = m.get("content", "")
        if not content:
            # Anthropic 400s on empty content — a deterministic 400 would burn
            # every key in the group via rotation, so drop the message instead.
            continue
        out.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    return out


def _body(request: ProviderRequest, *, stream: bool) -> dict[str, Any]:
    payload = request.payload
    body: dict[str, Any] = {
        "model": base.resolve_model(payload, ApiKeyProvider.CLAUDE),
        "max_tokens": int(payload.get("max_tokens", 4096)),
        "messages": _translate_messages(payload["messages"]),
    }
    if payload.get("system"):
        body["system"] = payload["system"]
    if payload.get("tools"):
        # Neutral schema {name, description, input_schema} == Anthropic's shape.
        body["tools"] = payload["tools"]
    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    if stream:
        body["stream"] = True
    return body


def _normalise(data: dict[str, Any]) -> dict[str, Any]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "arguments": block.get("input") or {},
                }
            )
    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "finish_reason": data.get("stop_reason"),
    }


class AnthropicAdapter:
    provider = ApiKeyProvider.CLAUDE

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        if request.capability is not ProviderCapability.LLM_CHAT:
            raise ValueError(f"claude does not serve {request.capability.value}")
        async with base.new_client() as client:
            resp = await client.post(
                _URL, json=_body(request, stream=False), headers=_headers(secret)
            )
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        usage = data.get("usage") or {}
        return ProviderCallResult(
            http_status=200,
            body=_normalise(data),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )

    async def stream(
        self, *, secret: str, request: ProviderRequest
    ) -> AsyncGenerator[StreamEvent, None]:
        if request.capability is not ProviderCapability.LLM_CHAT:
            raise ValueError(f"claude does not serve {request.capability.value}")
        text_parts: list[str] = []
        # tool_use blocks arrive as concatenated input_json_delta fragments,
        # keyed by content-block index until content_block_stop.
        tool_blocks: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None

        async with base.new_client(stream=True) as client:  # noqa: SIM117
            async with client.stream(
                "POST", _URL, json=_body(request, stream=True), headers=_headers(secret)
            ) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    yield StreamComplete(
                        ProviderCallResult(
                            http_status=resp.status_code, body=base.scrub_error(resp)
                        )
                    )
                    return
                async for data in base.iter_sse_lines(resp):
                    ev = json.loads(data)
                    etype = ev.get("type")
                    if etype == "message_start":
                        usage = (ev.get("message") or {}).get("usage") or {}
                        input_tokens = int(usage.get("input_tokens", 0))
                    elif etype == "content_block_start":
                        block = ev.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            tool_blocks[ev.get("index", 0)] = {
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "partial": "",
                            }
                    elif etype == "content_block_delta":
                        delta = ev.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            chunk = delta.get("text", "")
                            if chunk:
                                text_parts.append(chunk)
                                yield TokenDelta(chunk)
                        elif delta.get("type") == "input_json_delta":
                            blk = tool_blocks.get(ev.get("index", 0))
                            if blk is not None:
                                blk["partial"] += delta.get("partial_json", "")
                    elif etype == "message_delta":
                        finish_reason = (ev.get("delta") or {}).get("stop_reason", finish_reason)
                        usage = ev.get("usage") or {}
                        output_tokens = int(usage.get("output_tokens", output_tokens))
                    elif etype == "error":
                        # Mid-stream failure delivered inside an HTTP 200 — map
                        # it onto a synthetic non-2xx so the router classifies
                        # (rotate before first token / fail-visible after).
                        kind = (ev.get("error") or {}).get("type")
                        status = _STREAM_ERROR_STATUS.get(kind, 500)
                        yield StreamComplete(
                            ProviderCallResult(
                                http_status=status,
                                body=base.scrub_stream_error(status, kind),
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
                        )
                        return

        tool_calls = [
            {
                "id": blk["id"],
                "name": blk["name"],
                "arguments": _safe_json(blk["partial"]),
            }
            for blk in tool_blocks.values()
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


__all__ = ["AnthropicAdapter"]
