"""`MAX_CONTEXT_TOKEN_CAP` derivation (task dossier:
docs/tasks/2026-07-16-context-token-cap-upper-bound/, AC-5).

Pins the constant to `max(CONTEXT_LIMITS.values())` rather than its current numeric
value, so a future provider with a wider context window fails this test instead of
silently leaving the bound (and the 0057 migration's CHECK literal) too low.
"""

from __future__ import annotations

from contexts.agents.domain.models import CONTEXT_LIMITS, MAX_CONTEXT_TOKEN_CAP


def test_max_context_token_cap_is_derived_from_context_limits() -> None:
    assert max(CONTEXT_LIMITS.values()) == MAX_CONTEXT_TOKEN_CAP
