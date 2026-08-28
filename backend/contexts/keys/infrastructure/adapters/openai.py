"""OpenAI adapter — Responses API (streaming, tools) + Embeddings (K.1).

One adapter, two capabilities (LLM_CHAT, EMBEDDING) dispatched on
``request.capability`` — the router maps exactly one adapter per provider.
Auth via ``Authorization: Bearer``; model id is always caller-supplied.

Chat runs on ``/v1/responses``, not ``/v1/chat/completions``. The migration and
the shape differences behind every conditional below are recorded in
``docs/tasks/2026-08-27-openai-responses-api-migration/spec.md`` §5; the reason
it happened at all is that Chat Completions refuses ``reasoning_effort``
alongside function tools from gpt-5.4 onwards, and every SMAP agent turn sends
tools.
"""

from __future__ import annotations

import hashlib
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

_CHAT_URL = "https://api.openai.com/v1/responses"
_EMBED_URL = "https://api.openai.com/v1/embeddings"

# In-stream failures arrive under HTTP 200, either as a top-level `error` event
# or as `response.failed` carrying `response.error`. Both use a closed `code`
# vocabulary, so map it explicitly rather than by substring: the router turns
# this status into rotate-or-abort (`router_policy.classify_http`), and a
# misclassification either burns a whole key group or hammers the provider.
# An unrecognised code is a server fault, which rotates — the safe side.
_STREAM_ERROR_STATUS: dict[str, int] = {
    "rate_limit_exceeded": 429,
    "server_error": 500,
}
_STREAM_ERROR_DEFAULT_STATUS = 500


def _attachment_note(b: dict[str, Any]) -> dict[str, Any]:
    name = b.get("filename", "a file")
    mime = b.get("media_type", "?")
    return {
        "type": "input_text",
        "text": f"[User attached {name} ({mime}); this model cannot view it.]",
    }


def _content_parts(blocks: list[dict[str, Any]], *, vision: bool) -> list[dict[str, Any]]:
    """Neutral attachment blocks -> Responses input content parts."""
    parts: list[dict[str, Any]] = []
    for b in blocks:
        kind = b.get("type")
        if kind == "text":
            if b.get("text"):
                parts.append({"type": "input_text", "text": b["text"]})
        elif kind == "image" and vision:
            url = f"data:{b.get('media_type', 'image/png')};base64,{b.get('data', '')}"
            # `image_url` is a plain string here; Chat Completions nested it
            # under `{"url": ...}` and Responses 400s on the nested form.
            parts.append({"type": "input_image", "image_url": url})
        elif kind == "document" and vision:
            # PDF document understanding via an `input_file` part carrying a
            # base64 data URL. Same multimodal model set as vision; text-only
            # models still get the note below.
            data_url = f"data:{b.get('media_type', 'application/pdf')};base64,{b.get('data', '')}"
            parts.append(
                {
                    "type": "input_file",
                    "filename": b.get("filename", "attachment.pdf"),
                    "file_data": data_url,
                }
            )
        else:
            # Unsupported attachment, or any attachment for a text-only model -> note.
            parts.append(_attachment_note(b))
    return parts


def _headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def _key_tag(secret: str) -> str:
    """An opaque, non-reversible label for "the key this response came from".

    `reasoning.encrypted_content` is decryptable only by the account that
    produced it, and a key group legitimately holds keys from more than one
    OpenAI account. Each tool round is an independent `call_stream` that
    re-picks a group member, so round N+1 can run on a different key than round
    N — and replaying round N's items to it is a deterministic HTTP 400, which
    the router classifies as ABORT and which kills the whole group rather than
    rotating. Tagging the items lets the replay simply not happen instead.

    A truncated SHA-256 digest, never the secret: it stays in worker memory for
    one turn, is never logged, never persisted (``ProviderCallResult.body`` is
    not), and never reaches the provider — `_input_items` reads it and drops it.
    """
    return hashlib.sha256(secret.encode()).hexdigest()[:16]


def _input_items(payload: dict[str, Any], *, vision: bool, key_tag: str) -> list[dict[str, Any]]:
    """Neutral messages -> Responses `input` items.

    Responses thinks in items rather than messages, so one neutral message can
    expand into several: an assistant tool-use turn becomes its text plus one
    `function_call` item per call, and a tool result is a top-level
    `function_call_output` item rather than a `role: "tool"` message. The two
    halves are linked by `call_id`, which is why the neutral `tool_calls[].id`
    must be the provider's `call_id` and not the output item's own `id`.

    The system prompt is NOT handled here: it becomes the top-level
    `instructions` field in :func:`_responses_body`.
    """
    items: list[dict[str, Any]] = []
    for m in payload["messages"]:
        role = m.get("role")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id"),
                    "output": m.get("content", ""),
                }
            )
        elif role == "assistant" and m.get("tool_calls"):
            provider_items = m.get("provider_items")
            if provider_items and m.get("provider_items_key") == key_tag:
                # Replay the provider's own output items verbatim. They already
                # contain this turn's text and function calls, and — the reason
                # this path exists — its encrypted reasoning items, without
                # which a reasoning model loses its chain across a tool round
                # and silently degrades (spec §5.6 / Q-4).
                #
                # Only when the key matches the one that produced them: see
                # `_key_tag`. A mismatch falls through to the synthesised items
                # below, which is the pre-Q-4 behaviour — the model loses its
                # chain, rather than the request being rejected and the group
                # aborted.
                items.extend(provider_items)
                continue
            if m.get("content"):
                items.append({"role": "assistant", "content": m["content"]})
            items.extend(
                {
                    "type": "function_call",
                    "call_id": tc.get("id"),
                    "name": tc.get("name"),
                    "arguments": json.dumps(tc.get("arguments") or {}),
                }
                for tc in m["tool_calls"]
            )
        else:
            content = m.get("content", "")
            if isinstance(content, list):
                content = _content_parts(content, vision=vision)
            items.append({"role": role if role in ("user", "assistant") else "user", "content": content})
    return items


def _tools(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    tools = payload.get("tools")
    if not tools:
        return None
    return [
        {
            # Internal tagging: no `function` wrapper, unlike Chat Completions.
            "type": "function",
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            # Stated rather than inherited: agent-authored schemas are arbitrary
            # JSON Schema, and strict mode constrains their shape (every
            # property required, `additionalProperties: false`). Inheriting a
            # strict default would reject tools that work today.
            "strict": False,
        }
        for t in tools
    ]


def _responses_body(request: ProviderRequest, *, stream: bool, key_tag: str) -> dict[str, Any]:
    payload = request.payload
    model = base.resolve_model(payload, ApiKeyProvider.OPENAI)
    caps = base.capability_flags(payload)
    body: dict[str, Any] = {
        "model": model,
        "input": _input_items(payload, vision=caps.accepts_vision, key_tag=key_tag),
        # Retention is opt-OUT on this endpoint: omitting `store` leaves every
        # turn's content with OpenAI for at least 30 days, which Chat
        # Completions never did. Unconditional by design — this platform is
        # BYO-key and its rooms carry minors' first-person accounts.
        "store": False,
        # Pairs with the `provider_items` replay in `_input_items`: without
        # this, the reasoning items come back with no content to replay.
        "include": ["reasoning.encrypted_content"],
    }
    if payload.get("system"):
        # Responses carries the system prompt as a field, not as a message.
        body["instructions"] = payload["system"]
    if payload.get("max_tokens") is not None:
        # One field for every model. The Chat Completions split between
        # `max_tokens` and `max_completion_tokens` (which reasoning models 400
        # on) does not exist here, so `uses_completion_token_field` is unread.
        body["max_output_tokens"] = payload["max_tokens"]
    if caps.accepts_sampling:
        if payload.get("temperature") is not None:
            body["temperature"] = payload["temperature"]
        if payload.get("top_p") is not None:
            body["top_p"] = payload["top_p"]
        # `seed` is deliberately absent: the Responses API has no equivalent
        # parameter. Nothing reachable loses it — no catalogued OpenAI model
        # sets `accepts_sampling`, so this branch never ran for one.
    tools = _tools(payload)
    if tools:
        body["tools"] = tools
    # Cross-provider effort -> Responses `reasoning.effort`. Gated by membership
    # in effort_values (not just "the model accepts effort at all") via
    # CapabilityFlags.forwardable_effort. Unlike Chat Completions this composes
    # with `tools`, which is the whole point of the migration. The gate's
    # `effort_conflicts_with_tools` arm still applies, but no catalogued OpenAI
    # model sets it any more -- the conflict belonged to the old endpoint.
    effort = caps.forwardable_effort(payload.get("effort"))
    if effort is not None:
        body["reasoning"] = {"effort": effort}
    if stream:
        body["stream"] = True
        # No `stream_options`: usage arrives on the terminal `response.completed`
        # event's response object whether or not it is asked for.
    return body


def _finish_reason(data: dict[str, Any]) -> str | None:
    """Nearest equivalent of a Chat Completions finish reason.

    There is no such field on a Response. Truncation shows as
    ``status == "incomplete"`` plus ``incomplete_details.reason``. The raw
    provider value is returned unchanged — normalisation belongs to
    ``provider_router._TRUNCATED_FINISH_REASONS``, which carries
    ``max_output_tokens`` for exactly this reason.
    """
    status = data.get("status")
    if status == "incomplete":
        reason = (data.get("incomplete_details") or {}).get("reason")
        if reason:
            return str(reason)
    return str(status) if status else None


def _usage(data: dict[str, Any]) -> tuple[int, int]:
    """``(input_tokens, output_tokens)`` off a response object.

    ``output_tokens_details.reasoning_tokens`` is reported separately but is
    already counted inside ``output_tokens`` and billed as output, so adding it
    would double-charge the user.
    """
    usage = data.get("usage") or {}
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


def _normalise_response(data: dict[str, Any], *, key_tag: str) -> dict[str, Any]:
    """Response object -> the neutral chat body the turn engine consumes.

    Shared by the streaming and non-streaming paths: `response.completed`
    carries the same object the non-streaming call returns, so there is exactly
    one place where output items become `{text, tool_calls, finish_reason}`.
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    output = data.get("output") or []
    for item in output:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if part.get("type") == "output_text" and part.get("text"):
                    text_parts.append(part["text"])
        elif kind == "function_call":
            tool_calls.append(
                {
                    # `call_id`, not the item's own `id`: this is the value the
                    # matching `function_call_output` has to echo next round.
                    "id": item.get("call_id"),
                    "name": item.get("name"),
                    "arguments": _safe_json(item.get("arguments", "")),
                }
            )
    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "finish_reason": _finish_reason(data),
        # Opaque above this module: the turn engine copies both onto the
        # assistant turn without reading either, and `_input_items` replays the
        # items only when the tag still matches the key in hand. See Q-4 and
        # `_key_tag`.
        "provider_items": output,
        "provider_items_key": key_tag,
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
        key_tag = _key_tag(secret)
        async with base.new_client() as client:
            resp = await client.post(
                _CHAT_URL,
                json=_responses_body(request, stream=False, key_tag=key_tag),
                headers=_headers(secret),
            )
        if resp.status_code != 200:
            return ProviderCallResult(http_status=resp.status_code, body=base.scrub_error(resp))
        data = resp.json()
        input_tokens, output_tokens = _usage(data)
        if data.get("status") == "failed":
            # A run that fails *after* the request was accepted comes back as a
            # 200 whose `status` is `failed`, carrying an `error` and an empty
            # `output`. Chat Completions had no such shape — every failure there
            # was a non-2xx — so the status check above is not enough any more.
            # Unmapped, the router records a success, does not rotate, and the
            # caller consumes an empty string as the model's answer.
            return _failure_result((data.get("error") or {}).get("code"), input_tokens, output_tokens)
        return ProviderCallResult(
            http_status=200,
            body=_normalise_response(data, key_tag=key_tag),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
        """Drive one Responses SSE stream.

        Emits a `TokenDelta` per `response.output_text.delta` and exactly one
        terminal `StreamComplete`. Tool calls are NOT reassembled from
        `response.function_call_arguments.delta` fragments: the terminal
        response object carries every output item whole, so the fragments are
        only progress and the accumulation the Chat Completions path needed is
        gone.

        There is no `[DONE]` sentinel on this endpoint — the stream ends with a
        terminal event and the body closes — so falling out of the loop means
        the stream was cut, which is a failure and is reported as one.
        """
        if request.capability is not ProviderCapability.LLM_CHAT:
            raise ValueError(f"openai does not stream {request.capability.value}")

        key_tag = _key_tag(secret)
        async with base.new_client(stream=True) as client:  # noqa: SIM117
            async with client.stream(
                "POST",
                _CHAT_URL,
                json=_responses_body(request, stream=True, key_tag=key_tag),
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
                    kind = ev.get("type")
                    if kind == "response.output_text.delta":
                        chunk = ev.get("delta")
                        if chunk:
                            yield TokenDelta(chunk)
                    elif kind in ("response.completed", "response.incomplete"):
                        # `incomplete` is truncation, not an error: it carries a
                        # usable partial response plus incomplete_details.reason.
                        final = ev.get("response") or {}
                        input_tokens, output_tokens = _usage(final)
                        yield StreamComplete(
                            ProviderCallResult(
                                http_status=200,
                                body=_normalise_response(final, key_tag=key_tag),
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
                        )
                        return
                    elif kind == "response.failed":
                        failed = ev.get("response") or {}
                        code = (failed.get("error") or {}).get("code")
                        input_tokens, output_tokens = _usage(failed)
                        yield StreamComplete(_failure_result(code, input_tokens, output_tokens))
                        return
                    elif kind == "error":
                        yield StreamComplete(_failure_result(ev.get("code"), 0, 0))
                        return

        yield StreamComplete(_failure_result("incomplete_stream", 0, 0))


def _failure_result(code: object, input_tokens: int, output_tokens: int) -> ProviderCallResult:
    """A failure delivered under HTTP 200 -> synthetic non-2xx for the router.

    Two shapes reach here: an `error` event or `response.failed` mid-stream, and
    a non-streaming response whose `status` is `failed`. All three are the same
    thing — a run that failed after the request was accepted — and all three
    must be classified, not returned as an empty success.
    """
    status = _STREAM_ERROR_STATUS.get(str(code or ""), _STREAM_ERROR_DEFAULT_STATUS)
    return ProviderCallResult(
        http_status=status,
        body=base.scrub_stream_error(status, code),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
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
