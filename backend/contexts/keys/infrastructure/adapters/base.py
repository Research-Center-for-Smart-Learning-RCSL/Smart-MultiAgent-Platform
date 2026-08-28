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
                                        # an assistant turn that made tool calls
                                        #   may also carry "provider_items": a
                                        #   list the adapter that produced it put
                                        #   on its normalised body and replays
                                        #   verbatim. OPAQUE: no layer above the
                                        #   adapter reads it, and an adapter that
                                        #   did not write it MUST ignore it.
                                        #   OpenAI uses it to carry encrypted
                                        #   reasoning items across a tool round.
        "system": "optional system prompt",
        "tools": [{"name", "description", "input_schema"}],   # neutral schema
        "max_tokens": 4096,
        "effort": "low",                # optional; forwarded only where the
                                        #   capability fields below say so
        "temperature": 0.7,             # optional; forwarded only where the
                                        #   capability fields below say so
        "top_p": 1.0,                   # optional; same
        "seed": 42,                     # optional; NO adapter forwards it any
                                        #   more -- Anthropic/Gemini never had
                                        #   an equivalent and OpenAI's moved to
                                        #   /v1/responses, which has none either

        # Capability fields (R9.03a) -- resolved once per (provider, model) by
        # whichever agents-context call site builds the payload
        # (turn_engine._chat_request / RouterSummariser / AgentsFacade.
        # chat_model_capabilities), via contexts.agents.domain.model_specs.
        # No adapter re-derives these from the model id; a payload built
        # outside that path (none flags at all) takes every branch's safe
        # default, matching the conservative floor a table-absent model
        # resolves to (Q-2) -- omitting one of these is a silent
        # under-feature, not an error, so a 4th adapter or a new LLM_CHAT
        # call site MUST attach them or inherit Q-2's floor deliberately.
        "accepts_effort": True,         # gates whether "effort" is sent at all
        "effort_values": ("low", "medium", "high"),  # gates WHICH values
        "accepts_sampling": True,       # gates temperature/top_p/seed together
        "accepts_vision": True,         # image/document blocks vs. a text note
        "uses_completion_token_field": False,  # max_completion_tokens vs. the
                                                #   legacy max_tokens key. Both
                                                #   are Chat Completions names;
                                                #   the OpenAI adapter moved to
                                                #   /v1/responses and its single
                                                #   max_output_tokens, so no
                                                #   adapter reads this today.
        "effort_conflicts_with_tools": False,  # gpt-5.4+ on Chat Completions
                                                #   refused effort alongside
                                                #   tools. Still read (see
                                                #   forwardable_effort), but no
                                                #   catalogued model sets it any
                                                #   more: /v1/responses has no
                                                #   such conflict.
    }

Both flags are kept on the payload rather than removed: the gate they feed is
shared across adapters, and a future provider or endpoint may need them again.

EMBEDDING::

    {"model": "text-embedding-3-small", "input": ["text", ...]}

RERANK::

    {"model": "rerank-...", "query": "...", "documents": ["...", ...],
     "top_n": 5}

Normalised response (``ProviderCallResult.body``)
-------------------------------------------------
- chat ......  ``{"text": str, "tool_calls": [...], "finish_reason": str|None}``
               plus an optional opaque ``"provider_items"`` (see the payload
               note above) that the turn engine copies back onto the assistant
               turn it appends
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
from dataclasses import dataclass
from typing import Any

import httpx

from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes.base import safe_ident, summarise_http_failure

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


def scrub_stream_error(status: int, kind: object = None) -> dict[str, Any]:
    """Secret-free error body for an *in-stream* provider error event.

    Mirrors :func:`scrub_error`'s closed vocabulary (status + provider error
    type/code only) for errors delivered inside an HTTP-200 SSE stream — no
    URLs, headers, messages, or key material survive.

    *kind* goes through the same :func:`safe_ident` filter the non-2xx path
    applies, and for the same reason: it is provider-controlled text, and an
    error delivered inside an HTTP 200 is no more trustworthy than one that
    arrived with a status code. Every real value on all three adapters
    (`rate_limit_error`, `RESOURCE_EXHAUSTED`, `rate_limit_exceeded`) is
    identifier-shaped; a sentence, or a masked key reflection smuggled into
    the field, is dropped whole rather than truncated.
    """
    ident = safe_ident(kind)
    if ident is not None:
        return {"error": f"HTTP {status} ({ident})"}
    return {"error": f"HTTP {status}"}


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


@dataclass(frozen=True, slots=True)
class CapabilityFlags:
    """Capability-table fields (R9.03a) parsed off a ``ProviderRequest.payload``.

    Centralises both the extraction and the effort-forwarding gate every
    LLM_CHAT adapter needs, so a fix to the gate lands once. It was hand-applied
    to all three adapters exactly once already (D-5: forwarding gated on the
    coarse ``accepts_effort`` flag rather than membership in ``effort_values``,
    which would let a stored out-of-range effort value reproduce this table's
    own incident) — the risk :meth:`forwardable_effort` exists to close.
    """

    accepts_effort: bool
    effort_values: tuple[str, ...]
    accepts_sampling: bool
    accepts_vision: bool
    uses_completion_token_field: bool
    effort_conflicts_with_tools: bool

    def forwardable_effort(self, requested: object) -> str | None:
        """The effort value to actually send, or ``None`` to omit it entirely.

        Requires membership in ``effort_values`` (not just ``accepts_effort`` —
        ``agent.effort`` is stored independently of ``model_id``, so an agent
        can carry a value its *current* model never listed) and the absence of
        a tools conflict, resolved from ``(provider, model)`` alone rather than
        from whether *this* call carries tools — so a turn's tool rounds and
        its tools-free synthesis call get the same treatment (AC-16).
        """
        if requested in self.effort_values and not self.effort_conflicts_with_tools:
            return str(requested)
        return None


def capability_flags(payload: dict[str, Any]) -> CapabilityFlags:
    """Parse the six capability-table fields off a payload.

    Every flag defaults to its safe-off side when absent, matching the
    conservative floor a table-absent model resolves to (Q-2) — a payload
    built outside the agents-context path (e.g. a future LLM_CHAT call site
    that forgets to attach these) degrades rather than guesses.
    """
    return CapabilityFlags(
        accepts_effort=bool(payload.get("accepts_effort")),
        effort_values=tuple(payload.get("effort_values") or ()),
        accepts_sampling=bool(payload.get("accepts_sampling")),
        accepts_vision=bool(payload.get("accepts_vision")),
        uses_completion_token_field=bool(payload.get("uses_completion_token_field")),
        effort_conflicts_with_tools=bool(payload.get("effort_conflicts_with_tools")),
    )


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
    "CapabilityFlags",
    "capability_flags",
    "iter_sse_lines",
    "new_client",
    "resolve_model",
    "scrub_error",
    "scrub_stream_error",
]
