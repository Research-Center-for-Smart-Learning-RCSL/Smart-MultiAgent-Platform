"""Gemini adapter — generateContent (+ SSE stream) + batchEmbedContents (K.1).

LLM_CHAT + EMBEDDING. **The key is sent via the ``x-goog-api-key`` header,
never the query string** (K.1 contract) — so a transport error or log that
captures the URL can never leak the secret. Only ``?alt=sse`` rides the query
string. Model id is caller-supplied.
"""

from __future__ import annotations

import json
import uuid
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

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _headers(secret: str) -> dict[str, str]:
    return {"x-goog-api-key": secret, "content-type": "application/json"}


def _contents(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in payload["messages"]:
        role = m.get("role")
        if role == "tool":
            # Gemini keys a function response by the function *name*, not an id.
            out.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": m.get("name"),
                                "response": {"result": m.get("content", "")},
                            }
                        }
                    ],
                }
            )
            continue
        if role == "assistant" and m.get("tool_calls"):
            parts: list[dict[str, Any]] = []
            if m.get("content"):
                parts.append({"text": m["content"]})
            for tc in m["tool_calls"]:
                parts.append({"functionCall": {"name": tc.get("name"), "args": tc.get("arguments") or {}}})
            out.append({"role": "model", "parts": parts})
            continue
        content = m.get("content")
        grole = "model" if role == "assistant" else "user"
        if isinstance(content, str):
            out.append({"role": grole, "parts": [{"text": content}]})
        elif isinstance(content, list):
            out.append({"role": grole, "parts": content})
    return out


def _chat_body(payload: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"contents": _contents(payload)}
    if payload.get("system"):
        body["systemInstruction"] = {"parts": [{"text": payload["system"]}]}
    if payload.get("tools"):
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                    }
                    for t in payload["tools"]
                ]
            }
        ]
    gen: dict[str, Any] = {}
    if payload.get("max_tokens") is not None:
        gen["maxOutputTokens"] = payload["max_tokens"]
    if payload.get("temperature") is not None:
        gen["temperature"] = payload["temperature"]
    # Cross-provider effort -> Gemini thinking level (Gemini 2.5+/3+; the REST
    # enum is upper-case). An unsupported model rejects it as a normal error.
    if payload.get("effort"):
        gen["thinkingConfig"] = {"thinkingLevel": str(payload["effort"]).upper()}
    if gen:
        body["generationConfig"] = gen
    return body


def _parse_candidate(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
    cands = data.get("candidates") or []
    if not cands:
        return "", [], None
    cand = cands[0]
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in (cand.get("content") or {}).get("parts") or []:
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append(
                {
                    "id": f"gemini-{uuid.uuid4().hex[:12]}",
                    "name": fc.get("name"),
                    "arguments": fc.get("args") or {},
                }
            )
    return "".join(text_parts), tool_calls, cand.get("finishReason")


def _usage(data: dict[str, Any]) -> tuple[int, int]:
    meta = data.get("usageMetadata") or {}
    return int(meta.get("promptTokenCount", 0)), int(meta.get("candidatesTokenCount", 0))


class GeminiAdapter:
    provider = ApiKeyProvider.GEMINI

    async def invoke(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        if request.capability is ProviderCapability.EMBEDDING:
            return await self._embed(secret=secret, request=request)
        if request.capability is ProviderCapability.LLM_CHAT:
            return await self._chat(secret=secret, request=request)
        raise ValueError(f"gemini does not serve {request.capability.value}")

    async def _chat(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        model = base.resolve_model(request.payload, ApiKeyProvider.GEMINI)
        url = f"{_BASE}/{model}:generateContent"
        async with base.new_client() as client:
            resp = await client.post(url, json=_chat_body(request.payload), headers=_headers(secret))
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        text, tool_calls, finish = _parse_candidate(data)
        in_tok, out_tok = _usage(data)
        return ProviderCallResult(
            http_status=200,
            body={"text": text, "tool_calls": tool_calls, "finish_reason": finish},
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    async def _embed(self, *, secret: str, request: ProviderRequest) -> ProviderCallResult:
        payload = request.payload
        model = base.resolve_model(payload, ApiKeyProvider.GEMINI)
        url = f"{_BASE}/{model}:batchEmbedContents"
        body = {
            "requests": [
                {"model": f"models/{model}", "content": {"parts": [{"text": t}]}} for t in payload["input"]
            ]
        }
        async with base.new_client() as client:
            resp = await client.post(url, json=body, headers=_headers(secret))
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        return ProviderCallResult(
            http_status=200,
            body={"embeddings": [e["values"] for e in data.get("embeddings") or []]},
        )

    async def stream(self, *, secret: str, request: ProviderRequest) -> AsyncGenerator[StreamEvent, None]:
        if request.capability is not ProviderCapability.LLM_CHAT:
            raise ValueError(f"gemini does not stream {request.capability.value}")
        model = base.resolve_model(request.payload, ApiKeyProvider.GEMINI)
        url = f"{_BASE}/{model}:streamGenerateContent"
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        finish_reason: str | None = None
        input_tokens = 0
        output_tokens = 0

        async with base.new_client(stream=True) as client:  # noqa: SIM117
            async with client.stream(
                "POST",
                url,
                params={"alt": "sse"},
                json=_chat_body(request.payload),
                headers=_headers(secret),
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
                    if isinstance(err, dict) and not ev.get("candidates"):
                        # In-band failure object inside an HTTP 200 stream —
                        # map onto a synthetic non-2xx for router classification.
                        code = err.get("code")
                        status = code if isinstance(code, int) and 400 <= code < 600 else 500
                        yield StreamComplete(
                            ProviderCallResult(
                                http_status=status,
                                body=base.scrub_stream_error(status, err.get("status")),
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
                        )
                        return
                    if not ev.get("candidates"):
                        # Prompt blocked: no candidates ever arrive — surface the
                        # block reason instead of an empty "success".
                        block = (ev.get("promptFeedback") or {}).get("blockReason")
                        if block:
                            finish_reason = f"blocked:{block}"
                    chunk_text, chunk_tools, chunk_finish = _parse_candidate(ev)
                    if chunk_text:
                        text_parts.append(chunk_text)
                        yield TokenDelta(chunk_text)
                    tool_calls.extend(chunk_tools)
                    if chunk_finish:
                        finish_reason = chunk_finish
                    in_tok, out_tok = _usage(ev)
                    if in_tok:
                        input_tokens = in_tok
                    if out_tok:
                        output_tokens = out_tok

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


__all__ = ["GeminiAdapter"]
