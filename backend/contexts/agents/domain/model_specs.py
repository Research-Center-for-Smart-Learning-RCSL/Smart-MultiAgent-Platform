"""Per-model provider capability table (R9.03a).

Replaces the three per-provider dictionaries that used to live in ``models.py``
(``CHAT_MODEL_CATALOG``, ``DEFAULT_CHAT_MODELS``, ``CONTEXT_LIMITS``) and the five
per-model regexes that used to live inside the OpenAI/Anthropic/Gemini adapters.
One row per catalogued model states its context window and which optional
request parameters it accepts, with a source and verification date attached to
each row rather than to the whole table.

A model id absent from the table (a BYO-key user's custom choice, or a model
the platform has not catalogued yet) resolves through :func:`resolve_spec` to a
conservative floor: no optional parameter is sent, and the provider's lowest
catalogued context window applies (Q-2). Guessing generously from an id pattern
is exactly the mechanism that produced the incident this table replaces.

The adapters live in ``contexts.keys.infrastructure``, which cannot import this
module (``shared_kernel`` may import a context; a context may not import
another context's domain). The capability facts therefore travel on the
``ProviderRequest`` payload — see :func:`capability_fields` — populated by
whichever agents-context call site builds the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# NOTE(AC-11 / AC-15, deferred 2026-08-28): this table's model lineup and effort
# value sets are carried forward from the pre-existing per-provider dictionaries
# and adapter regexes (i.e. re-expressed, not re-verified), because reconciling
# against each provider's live `models` endpoint needs a real provider key that
# was not made available this session. Source URLs point at each provider's
# model-documentation index rather than a per-model page for the same reason.
# See docs/tasks/2026-08-27-provider-model-capability-table/spec.md FU-11/FU-12.
_UNVERIFIED_DATE = "2026-06-01"

_CLAUDE_DOCS = "https://docs.anthropic.com/en/docs/about-claude/models/overview"
_OPENAI_DOCS = "https://platform.openai.com/docs/models"
_GEMINI_DOCS = "https://ai.google.dev/gemini-api/docs/models"


@dataclass(frozen=True, slots=True)
class ChatModelSpec:
    """One model's request-shaping contract.

    ``effort_values`` is empty when ``accepts_effort`` is False. ``source_url``
    and ``verified_on`` are per-row provenance (Q-4) — a table-wide comment
    cannot say which row a later reader should distrust.

    Two fields describe OpenAI's Chat Completions endpoint, which no adapter
    posts to any more, and they went dead in different ways.
    ``uses_completion_token_field`` still records which models renamed
    ``max_tokens`` there, and those values are left as they were, but nothing
    reads the field now: ``/v1/responses`` has one ``max_output_tokens``.
    ``effort_conflicts_with_tools`` is still read
    (``CapabilityFlags.forwardable_effort``) and had to be cleared instead:
    every row now sets it False, because the gpt-5.4+ conflict it was added for
    is a Chat Completions behaviour, and a row that still declared it would go
    on dropping the effort the migration exists to deliver. See
    ``docs/tasks/2026-08-27-openai-responses-api-migration/spec.md`` D-1.
    """

    model_id: str
    provider: str
    context_limit: int
    accepts_effort: bool
    effort_values: tuple[str, ...]
    accepts_sampling: bool
    accepts_vision: bool
    uses_completion_token_field: bool
    effort_conflicts_with_tools: bool
    source_url: str
    verified_on: str


# Effort acceptance / sampling acceptance / vision / completion-field / tools-conflict
# below are re-derived from the adapter regexes this table replaces (openai.py's
# `_REASONING_MODEL_RE`/`_VISION_MODEL_RE`/`_ALLOWS_EFFORT_WITH_TOOLS_RE`,
# anthropic.py's `_NO_SAMPLING_RE`/`_SUPPORTS_EFFORT_RE`), not invented fresh, so
# behaviour for every catalogued model is unchanged by this migration.
CHAT_MODEL_SPECS: tuple[ChatModelSpec, ...] = (
    ChatModelSpec(
        model_id="claude-opus-4-8",
        provider="claude",
        context_limit=1_000_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=False,
        accepts_vision=True,
        uses_completion_token_field=False,
        effort_conflicts_with_tools=False,
        source_url=_CLAUDE_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="claude-sonnet-4-6",
        provider="claude",
        context_limit=1_000_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=True,
        accepts_vision=True,
        uses_completion_token_field=False,
        effort_conflicts_with_tools=False,
        source_url=_CLAUDE_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="claude-haiku-4-5",
        provider="claude",
        context_limit=200_000,
        accepts_effort=False,
        effort_values=(),
        accepts_sampling=True,
        accepts_vision=True,
        uses_completion_token_field=False,
        effort_conflicts_with_tools=False,
        source_url=_CLAUDE_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="gpt-5.5",
        provider="openai",
        context_limit=128_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=False,
        accepts_vision=True,
        uses_completion_token_field=True,
        # False since the Responses API migration: the conflict was a Chat
        # Completions behaviour, and that is no longer the endpoint SMAP uses.
        effort_conflicts_with_tools=False,
        source_url=_OPENAI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="gpt-5.4",
        provider="openai",
        context_limit=128_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=False,
        accepts_vision=True,
        uses_completion_token_field=True,
        # See gpt-5.5 above. This is the row the 結書 incident was diagnosed on,
        # and clearing it is what re-enables the effort control the capability
        # table disabled.
        effort_conflicts_with_tools=False,
        source_url=_OPENAI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="gpt-5.4-mini",
        provider="openai",
        context_limit=128_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=False,
        accepts_vision=True,
        uses_completion_token_field=True,
        # See gpt-5.5 above.
        effort_conflicts_with_tools=False,
        source_url=_OPENAI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    # The o-series (reasoning models predating gpt-5) was never in
    # CHAT_MODEL_CATALOG's preset list either, before or after this table --
    # but the deleted `_REASONING_MODEL_RE = ^(?:o\d|gpt-5)` and
    # `_ALLOWS_EFFORT_WITH_TOOLS_RE` (which unconditionally allows `o\d`)
    # correctly shaped requests for ANY o-series id regardless of catalog
    # membership. Cataloguing these two restores that: an o-series id is no
    # longer forced through Q-2's floor (which would send the legacy
    # `max_tokens` field a reasoning endpoint 400s on -- the same incident
    # class this table exists to prevent, for a family the old code already
    # got right). Re-derived from the same deleted regexes as the rest of this
    # table, not freshly verified; carries the same AC-11 caveat.
    ChatModelSpec(
        model_id="o3",
        provider="openai",
        context_limit=128_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=False,
        accepts_vision=True,
        uses_completion_token_field=True,
        effort_conflicts_with_tools=False,
        source_url=_OPENAI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="o3-mini",
        provider="openai",
        context_limit=128_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=False,
        accepts_vision=True,
        uses_completion_token_field=True,
        effort_conflicts_with_tools=False,
        source_url=_OPENAI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="gemini-3.5-flash",
        provider="gemini",
        context_limit=1_000_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=True,
        accepts_vision=True,
        uses_completion_token_field=False,
        effort_conflicts_with_tools=False,
        source_url=_GEMINI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="gemini-2.5-pro",
        provider="gemini",
        context_limit=1_000_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=True,
        accepts_vision=True,
        uses_completion_token_field=False,
        effort_conflicts_with_tools=False,
        source_url=_GEMINI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
    ChatModelSpec(
        model_id="gemini-2.5-flash",
        provider="gemini",
        context_limit=1_000_000,
        accepts_effort=True,
        effort_values=("low", "medium", "high"),
        accepts_sampling=True,
        accepts_vision=True,
        uses_completion_token_field=False,
        effort_conflicts_with_tools=False,
        source_url=_GEMINI_DOCS,
        verified_on=_UNVERIFIED_DATE,
    ),
)

DEFAULT_MODEL_IDS: dict[str, str] = {
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-5.4",
    "gemini": "gemini-3.5-flash",
}


def _by_provider() -> dict[str, tuple[ChatModelSpec, ...]]:
    grouped: dict[str, list[ChatModelSpec]] = {}
    for spec in CHAT_MODEL_SPECS:
        grouped.setdefault(spec.provider, []).append(spec)
    return {provider: tuple(specs) for provider, specs in grouped.items()}


_SPECS_BY_PROVIDER: dict[str, tuple[ChatModelSpec, ...]] = _by_provider()

# Keyed on (provider, normalised model_id) -- restores the case/whitespace
# tolerance the five deleted regexes had (they matched against
# `model.strip().lower()`, never the raw id) without restoring their real
# defect, guessing an unlisted id's family from a prefix pattern. An exact
# normalised match is still a verified row; a near-miss still floors (Q-2).
_SPECS_BY_NORMALISED_ID: dict[tuple[str, str], ChatModelSpec] = {
    (spec.provider, spec.model_id.strip().lower()): spec for spec in CHAT_MODEL_SPECS
}

assert set(DEFAULT_MODEL_IDS) == set(_SPECS_BY_PROVIDER), (
    "DEFAULT_MODEL_IDS and CHAT_MODEL_SPECS must cover identical provider keys"
)
assert all(
    any(spec.model_id == default_id for spec in _SPECS_BY_PROVIDER[provider])
    for provider, default_id in DEFAULT_MODEL_IDS.items()
), "every DEFAULT_MODEL_IDS value must be a catalogued spec of its provider"


def specs_for_provider(provider: str) -> tuple[ChatModelSpec, ...]:
    """Every catalogued spec for one provider, in table order."""
    return _SPECS_BY_PROVIDER.get(provider, ())


def _conservative_floor(provider: str, model_id: str) -> ChatModelSpec:
    """Q-2's floor for a model id outside the table: no optional parameter is
    sent, and the provider's lowest catalogued context window applies."""
    provider_specs = _SPECS_BY_PROVIDER.get(provider, ())
    floor_context = min((spec.context_limit for spec in provider_specs), default=128_000)
    return ChatModelSpec(
        model_id=model_id,
        provider=provider,
        context_limit=floor_context,
        accepts_effort=False,
        effort_values=(),
        accepts_sampling=False,
        accepts_vision=False,
        uses_completion_token_field=False,
        effort_conflicts_with_tools=False,
        source_url="",
        verified_on="",
    )


def resolve_spec(provider: str, model_id: str) -> ChatModelSpec:
    """The catalogued spec for ``(provider, model_id)``, or the conservative
    floor (Q-2) when the id is not in the table.

    Matches case/whitespace-insensitively (a user-supplied id is free-text,
    not a whitelist selection), but never guesses a family from a prefix --
    an id that isn't a real row, spelled however, gets the floor.
    """
    spec = _SPECS_BY_NORMALISED_ID.get((provider, model_id.strip().lower()))
    if spec is not None:
        return spec
    return _conservative_floor(provider, model_id)


def capability_fields(spec: ChatModelSpec) -> dict[str, Any]:
    """The subset of ``spec`` an adapter needs, shaped for the
    ``ProviderRequest.payload`` dict (K.1's cross-context transport — see the
    module docstring)."""
    return {
        "accepts_effort": spec.accepts_effort,
        "effort_values": spec.effort_values,
        "accepts_sampling": spec.accepts_sampling,
        "accepts_vision": spec.accepts_vision,
        "uses_completion_token_field": spec.uses_completion_token_field,
        "effort_conflicts_with_tools": spec.effort_conflicts_with_tools,
    }


__all__ = [
    "CHAT_MODEL_SPECS",
    "DEFAULT_MODEL_IDS",
    "ChatModelSpec",
    "capability_fields",
    "resolve_spec",
    "specs_for_provider",
]
