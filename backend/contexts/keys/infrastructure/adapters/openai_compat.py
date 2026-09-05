"""OpenAI-compatible adapter -- Chat Completions + Embeddings (K.1, R7.16).

Serves any endpoint implementing the OpenAI Chat Completions wire protocol
(``/v1/chat/completions``) and optionally ``/v1/embeddings``. The base URL
and timeout come from ``request.provider_config`` (per-key ``config``).

Uses only the universally supported subset of Chat Completions:
- ``tool_choice`` is always ``"auto"`` (Q-9: many gateways refuse
  ``"required"`` or named functions)
- No ``provider_items`` passthrough (no encrypted reasoning items)
- No vendor-specific extensions
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from contexts.keys.application.provider_router import (
    ProviderCallResult,
    ProviderRequest,
    StreamComplete,
    StreamEvent,
    TokenDelta,
)
from contexts.keys.domain.providers import ApiKeyProvider, ProviderCapability
from contexts.keys.infrastructure.adapters import base
from contexts.keys.infrastructure.probes.base import validate_base_url


def _headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def _base_url(request: ProviderRequest) -> str:
    cfg = request.provider_config or {}
    url = cfg.get("base_url", "")
    if not url:
        raise ValueError("provider_config.base_url is required for openai_compat")
    return str(url).rstrip("/")


def _timeout(request: ProviderRequest) -> httpx.Timeout:
    cfg = request.provider_config or {}
    seconds = cfg.get("timeout_s", 120)
    return httpx.Timeout(float(seconds), connect=10.0)


def _stream_timeout(request: ProviderRequest) -> httpx.Timeout:
    cfg = request.provider_config or {}
    seconds = max(cfg.get("timeout_s", 300), 300)
    return httpx.Timeout(float(seconds), connect=10.0)


def _attachment_note(b: dict[str, Any]) -> str:
    name = b.get("filename", "a file")
    mime = b.get("media_type", "?")
    return f"[User attached {name} ({mime}); this model cannot view it.]"


def _content_parts(blocks: list[dict[str, Any]], *, vision: bool) -> list[dict[str, Any]]:
    """Neutral attachment blocks -> Chat Completions content parts."""
    parts: list[dict[str, Any]] = []
    for b in blocks:
        kind = b.get("type")
        if kind == "text":
            if b.get("text"):
                parts.append({"type": "text", "text": b["text"]})
        elif kind == "image" and vision:
            url = f"data:{b.get('media_type', 'image/png')};base64,{b.get('data', '')}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
        elif kind == "document" and vision:
            data_url = f"data:{b.get('media_type', 'application/pdf')};base64,{b.get('data', '')}"
            parts.append({"type": "text", "text": f"[Attached document: {b.get('filename', 'file')}]"})
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
        else:
            parts.append({"type": "text", "text": _attachment_note(b)})
    return parts


def _messages(payload: dict[str, Any], *, vision: bool) -> list[dict[str, Any]]:
    """Neutral messages -> Chat Completions messages array."""
    msgs: list[dict[str, Any]] = []
    if payload.get("system"):
        msgs.append({"role": "system", "content": payload["system"]})
    for m in payload.get("messages", []):
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
            tc_list = [
                {
                    "id": tc.get("id"),
                    "type": "function",
                    "function": {
                        "name": tc.get("name"),
                        "arguments": json.dumps(tc.get("arguments") or {}),
                    },
                }
                for tc in m["tool_calls"]
            ]
            msg: dict[str, Any] = {"role": "assistant", "tool_calls": tc_list}
            if m.get("content"):
                msg["content"] = m["content"]
            msgs.append(msg)
        else:
            content = m.get("content", "")
            if isinstance(content, list):
                content = _content_parts(content, vision=vision)
            msgs.append(
                {
                    "role": role if role in ("user", "assistant", "system") else "user",
                    "content": content,
                }
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
    model = base.resolve_model(payload, ApiKeyProvider.OPENAI_COMPAT)
    caps = base.capability_flags(payload)
    body: dict[str, Any] = {
        "model": model,
        "messages": _messages(payload, vision=caps.accepts_vision),
    }
    if payload.get("max_tokens") is not None:
        if caps.uses_completion_token_field:
            body["max_completion_tokens"] = payload["max_tokens"]
        else:
            body["max_tokens"] = payload["max_tokens"]
    if caps.accepts_sampling:
        if payload.get("temperature") is not None:
            body["temperature"] = payload["temperature"]
        if payload.get("top_p") is not None:
            body["top_p"] = payload["top_p"]
    if caps.accepts_seed and payload.get("seed") is not None:
        body["seed"] = payload["seed"]
    effort = caps.forwardable_effort(payload.get("effort"))
    if effort is not None:
        body["reasoning_effort"] = effort
    tools = _tools(payload)
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


def _normalise_chat(data: dict[str, Any]) -> dict[str, Any]:
    """Chat Completions response -> neutral body."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    tool_calls: list[dict[str, Any]] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        tool_calls.append(
            {
                "id": tc.get("id"),
                "name": fn.get("name"),
                "arguments": _safe_json(fn.get("arguments", "")),
            }
        )
    return {
        "text": text,
        "tool_calls": tool_calls,
        "finish_reason": choice.get("finish_reason"),
    }


def _usage(data: dict[str, Any]) -> tuple[int, int]:
    usage = data.get("usage") or {}
    return (
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
    )


class OpenAICompatAdapter:
    provider = ApiKeyProvider.OPENAI_COMPAT

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        if request.capability is ProviderCapability.EMBEDDING:
            return await self._embed(secret=secret, request=request)
        if request.capability is ProviderCapability.LLM_CHAT:
            return await self._chat(secret=secret, request=request)
        raise ValueError(f"openai_compat does not serve {request.capability.value}")

    async def _chat(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        url = f"{_base_url(request)}/v1/chat/completions"
        validate_base_url(_base_url(request))
        timeout = _timeout(request)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=_chat_body(request, stream=False), headers=_headers(secret))
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        input_tokens, output_tokens = _usage(data)
        return ProviderCallResult(
            http_status=200,
            body=_normalise_chat(data),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _embed(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        payload = request.payload
        url = f"{_base_url(request)}/v1/embeddings"
        validate_base_url(_base_url(request))
        body = {
            "model": base.resolve_model(payload, ApiKeyProvider.OPENAI_COMPAT),
            "input": payload["input"],
        }
        timeout = _timeout(request)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body, headers=_headers(secret))
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
            raise ValueError(f"openai_compat does not stream {request.capability.value}")

        url = f"{_base_url(request)}/v1/chat/completions"
        validate_base_url(_base_url(request))
        timeout = _stream_timeout(request)

        # Accumulate tool call fragments across deltas.
        tool_accum: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None

        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST",
                url,
                json=_chat_body(request, stream=True),
                headers=_headers(secret),
            ) as resp,
        ):
            if resp.status_code != 200:
                await resp.aread()
                yield StreamComplete(
                    ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
                )
                return
            async for data_str in base.iter_sse_lines(resp):
                ev = json.loads(data_str)
                if ev.get("error"):
                    err = ev["error"]
                    kind = err.get("type") or err.get("code")
                    yield StreamComplete(
                        ProviderCallResult(
                            http_status=500,
                            body=base.scrub_stream_error(500, kind),
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
                    )
                    return
                # Usage chunk (stream_options.include_usage)
                usage = ev.get("usage")
                if usage:
                    input_tokens = int(usage.get("prompt_tokens", 0))
                    output_tokens = int(usage.get("completion_tokens", 0))
                choices = ev.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                fr = choice.get("finish_reason")
                if fr:
                    finish_reason = fr
                # Text delta
                chunk = delta.get("content")
                if chunk:
                    yield TokenDelta(chunk)
                # Tool call deltas
                for tc_delta in delta.get("tool_calls") or []:
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_accum:
                        tool_accum[idx] = {
                            "id": tc_delta.get("id", ""),
                            "name": "",
                            "arguments": "",
                        }
                    fn = tc_delta.get("function") or {}
                    if fn.get("name"):
                        tool_accum[idx]["name"] = fn["name"]
                    if fn.get("arguments"):
                        tool_accum[idx]["arguments"] += fn["arguments"]

        # Assemble terminal result.
        tool_calls = [
            {
                "id": tc["id"],
                "name": tc["name"],
                "arguments": _safe_json(tc["arguments"]),
            }
            for _, tc in sorted(tool_accum.items())
        ]
        body: dict[str, Any] = {
            "text": "",
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
        }
        yield StreamComplete(
            ProviderCallResult(
                http_status=200,
                body=body,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )


def _safe_json(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["OpenAICompatAdapter"]
