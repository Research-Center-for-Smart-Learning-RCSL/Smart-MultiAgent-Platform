"""Per-model provider capability table (R9.03a, task dossier
docs/tasks/2026-08-27-provider-model-capability-table/).

AC-1: CHAT_MODEL_CATALOG / DEFAULT_CHAT_MODELS / CONTEXT_LIMITS are derived
views over the per-model specs, not independently authored constants.
AC-2: `_context_limit_for`-equivalent resolution is correct per model, not per
provider, for the Claude family the old provider-only lookup got wrong.
AC-3: a model id outside the table resolves to Q-2's conservative floor.
"""

from __future__ import annotations

from contexts.agents.domain.model_specs import (
    CHAT_MODEL_SPECS,
    DEFAULT_MODEL_IDS,
    capability_fields,
    resolve_spec,
    specs_for_provider,
)
from contexts.agents.domain.models import (
    CHAT_MODEL_CATALOG,
    CONTEXT_LIMITS,
    DEFAULT_CHAT_MODELS,
    AgentEffort,
    chat_model_catalog,
)


def test_chat_model_catalog_derives_from_specs() -> None:
    for provider, models in CHAT_MODEL_CATALOG.items():
        assert models == tuple(s.model_id for s in specs_for_provider(provider))


def test_every_providers_default_is_a_catalogued_model_of_that_provider() -> None:
    for provider, default in DEFAULT_CHAT_MODELS.items():
        assert default in CHAT_MODEL_CATALOG[provider]


def test_context_limits_is_the_widest_catalogued_window_per_provider() -> None:
    for provider, specs in ((p, specs_for_provider(p)) for p in DEFAULT_MODEL_IDS):
        assert CONTEXT_LIMITS[provider] == max(s.context_limit for s in specs)


def test_chat_model_catalog_entries_expose_full_specs() -> None:
    entries = {e.provider: e for e in chat_model_catalog()}
    assert set(entries) == set(DEFAULT_MODEL_IDS)
    for provider, entry in entries.items():
        assert entry.default == DEFAULT_MODEL_IDS[provider]
        assert entry.models == specs_for_provider(provider)


def test_claude_context_limit_is_per_model_not_per_provider() -> None:
    # AC-2: the pre-existing bug capped every Claude agent at 200_000 by
    # resolving from `model_hint` alone. Sonnet 4.6 carries 1_000_000; only
    # Haiku 4.5 is really 200_000.
    assert resolve_spec("claude", "claude-sonnet-4-6").context_limit == 1_000_000
    assert resolve_spec("claude", "claude-opus-4-8").context_limit == 1_000_000
    assert resolve_spec("claude", "claude-haiku-4-5").context_limit == 200_000


def test_haiku_does_not_accept_effort_but_accepts_sampling() -> None:
    # The exact shape that produced the 結書 incident: Haiku 4.5 ships in the
    # catalogue and 400s on `output_config.effort`.
    spec = resolve_spec("claude", "claude-haiku-4-5")
    assert spec.accepts_effort is False
    assert spec.effort_values == ()
    assert spec.accepts_sampling is True


def test_gpt_5_4_accepts_effort_but_conflicts_with_tools() -> None:
    spec = resolve_spec("openai", "gpt-5.4")
    assert spec.accepts_effort is True
    assert spec.effort_conflicts_with_tools is True
    assert spec.uses_completion_token_field is True
    assert spec.accepts_sampling is False


def test_o_series_is_catalogued_and_uses_the_completion_token_field() -> None:
    # The o-series predates gpt-5 and was never in the UI preset list, but the
    # deleted `_REASONING_MODEL_RE = ^(?:o\d|gpt-5)` correctly shaped requests
    # for ANY o-series id -- catalogued here so it isn't forced through Q-2's
    # floor, which would send the legacy `max_tokens` field a reasoning
    # endpoint 400s on.
    for model_id in ("o3", "o3-mini"):
        spec = resolve_spec("openai", model_id)
        assert spec.uses_completion_token_field is True
        assert spec.accepts_effort is True
        assert spec.effort_conflicts_with_tools is False
        assert spec.accepts_sampling is False


def test_resolve_spec_matches_case_and_padding_insensitively() -> None:
    # `model_id` is user-supplied free text, not a whitelist selection -- the
    # five deleted regexes matched against `model.strip().lower()`, never the
    # raw id, and this restores that tolerance without restoring their real
    # defect (guessing a family from a prefix pattern for an id that isn't a
    # real row at all).
    canonical = resolve_spec("claude", "claude-opus-4-8")
    assert resolve_spec("claude", "  Claude-Opus-4-8  ") == canonical


def test_unknown_model_id_resolves_to_the_conservative_floor() -> None:
    # AC-3: no effort, no sampling, and the provider's lowest catalogued
    # context window -- never a guess from the id's shape.
    spec = resolve_spec("openai", "gpt-99-nonexistent")
    assert spec.accepts_effort is False
    assert spec.effort_values == ()
    assert spec.accepts_sampling is False
    assert spec.accepts_vision is False
    assert spec.uses_completion_token_field is False
    assert spec.effort_conflicts_with_tools is False
    assert spec.context_limit == min(s.context_limit for s in specs_for_provider("openai"))

    fields = capability_fields(spec)
    assert not any(fields[k] for k in ("accepts_effort", "accepts_sampling", "accepts_vision"))


def test_unknown_provider_floors_to_a_default_context_limit() -> None:
    # An id under a provider string with no catalogued specs at all (should
    # not occur via the enum-backed model_hint, but resolve_spec must not
    # raise on it) falls back to a fixed floor rather than an empty min().
    spec = resolve_spec("nonexistent-provider", "whatever")
    assert spec.context_limit == 128_000
    assert spec.accepts_effort is False


def test_source_and_verification_date_are_recorded_per_row() -> None:
    # Q-4: provenance is per-row, not a table-wide comment.
    for spec in CHAT_MODEL_SPECS:
        assert spec.source_url, spec.model_id
        assert spec.verified_on, spec.model_id


def test_agent_effort_widened_to_the_cross_provider_union() -> None:
    # Q-3: the union of every value any provider accepts.
    assert {e.value for e in AgentEffort} == {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }
