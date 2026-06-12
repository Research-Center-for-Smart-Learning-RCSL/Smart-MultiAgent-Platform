"""Shared plumbing for provider adapters (K.1).

Every adapter conforms to the `ProviderAdapter` (and, for chat, the
`StreamingAdapter`) seam declared in
``contexts.keys.application.provider_router``. The router hands each adapter a
plaintext secret plus a provider-agnostic :class:`ProviderRequest`; the adapter
shapes the provider-specific HTTP call and normalises the response back into a
:class:`ProviderCallResult` the router can account and classify.

Canonical request payload (``ProviderRequest.payload``)
-------------------------------------------------------
LLM_CHAT::

    {
        "model": "claude-...",          # the agent's configured model (R9.x);
        "models": {"claude": "..."},    # OR a per-provider map (multi-provider
                                        #   key groups, e.g. graphrag builder)
        "messages": [{"role": "user"|"assistant", "content": "..."}],
        "system": "optional system prompt",
        "tools": [{"name", "description", "input_schema"}],   # neutral schema
        "max_tokens": 4096,
        "temperature": 0.7,             # optional
    }

EMBEDDING::

    {"model": "text-embedding-3-small", "input": ["text", ...]}

RERANK::

    {"model": "rerank-...", "query": "...", "documents": ["...", ...],
     "top_n": 5}

Normalised response (``ProviderCallResult.body``)
-------------------------------------------------
- chat ......  ``{"text": str, "tool_calls": [...], "finish_reason": str|None}``
- embedding .  ``{"embeddings": [[float, ...], ...]}``  (input order preserved)
- rerank ....  ``{"results": [{"index": int, "relevance_score": float}, ...]}``

Security
--------
A non-2xx response NEVER raises and NEVER echoes the secret: the adapter
returns a :class:`ProviderCallResult` whose ``body`` is the closed-vocabulary
:func:`scrub_error` output (status + provider error *type/code* only, via the
probes' :func:`summarise_http_failure`). The model id is caller-supplied — no
adapter hardcodes one (K.1 contract).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes.base import summarise_http_failure

# Agent turns can run long (tool rounds, big contexts); far past the 5 s probe
# circuit-breaker. The arq worker — never the web process — owns these calls.
DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


def new_client(*, stream: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=STREAM_TIMEOUT if stream else DEFAULT_TIMEOUT)


def scrub_error(resp: httpx.Response) -> dict[str, Any]:
    """Secret-free error body for a non-2xx response.

    Delegates to the audited probe scrubber so no key material, URL, or raw
    provider HTML survives into logs / usage rows / the chat UI.
    """
    return {"error": summarise_http_failure(resp)}


def resolve_model(payload: dict[str, Any], provider: ApiKeyProvider) -> str:
    """Caller-supplied model id; never hardcoded (K.1 contract).

    Accepts either an explicit ``model`` (agent turns — one model per agent)
    or a ``models`` map keyed by provider value (multi-provider key groups
    where the router picks the key, e.g. the graphrag builder group).
    """
    model = payload.get("model")
    if not model:
        models = payload.get("models")
        if isinstance(models, dict):
            model = models.get(provider.value)
    if not model:
        raise ValueError(f"no model configured for provider {provider.value!r}")
    return str(model)


async def iter_sse_lines(resp: httpx.Response) -> AsyncIterator[str]:
    """Yield decoded ``data:`` payloads from a text/event-stream response.

    Skips comments / blank separators; stops on the ``[DONE]`` sentinel that
    OpenAI emits. Each yielded string is the raw JSON of one SSE ``data:``
    field (callers ``json.loads`` it).
    """
    async for raw in resp.aiter_lines():
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        yield data


__all__ = [
    "DEFAULT_TIMEOUT",
    "STREAM_TIMEOUT",
    "iter_sse_lines",
    "new_client",
    "resolve_model",
    "scrub_error",
]
