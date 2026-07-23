"""Unit tests for the single-source claim-TTL module (FU-3)."""

from __future__ import annotations

from contexts.workflow.domain import claim_ttl as ct


def test_consumer_budget_is_attempts_times_delay() -> None:
    assert ct.CLAIM_CONSUMER_BUDGET_S == ct.CLAIM_RESUME_MAX_ATTEMPTS * ct.CLAIM_RESUME_DELAY_S
    assert ct.CLAIM_CONSUMER_BUDGET_S == 630


def test_grace_constants_preserve_current_values() -> None:
    # Behaviour-preserving refactor: the values the four producers used inline.
    assert ct.GATE_CLAIM_GRACE_S == 300
    assert ct.WAIT_CLAIM_GRACE_S == 60


def test_initial_claim_ttl_is_timeout_plus_grace() -> None:
    assert ct.initial_claim_ttl(600, ct.GATE_CLAIM_GRACE_S) == 900
    assert ct.initial_claim_ttl(600, ct.WAIT_CLAIM_GRACE_S) == 660
    # Coerces a str-ish timeout the way approval_gate did (int(timeout_seconds)).
    assert ct.initial_claim_ttl("120", ct.GATE_CLAIM_GRACE_S) == 420


def test_remaining_budget_ttl_decays_but_stays_above_delay() -> None:
    at0 = ct.remaining_budget_ttl(ct.CLAIM_RESUME_MAX_ATTEMPTS, ct.CLAIM_RESUME_DELAY_S, 0)
    at_last = ct.remaining_budget_ttl(
        ct.CLAIM_RESUME_MAX_ATTEMPTS, ct.CLAIM_RESUME_DELAY_S, ct.CLAIM_RESUME_MAX_ATTEMPTS
    )
    assert at0 == (ct.CLAIM_RESUME_MAX_ATTEMPTS + 1) * ct.CLAIM_RESUME_DELAY_S
    assert at0 > ct.CLAIM_CONSUMER_BUDGET_S
    # Never drops to zero while a retry remains — the invariant behind I2.
    assert at_last == ct.CLAIM_RESUME_DELAY_S


def test_domain_module_has_no_upward_imports() -> None:
    # AC-4: domain must not reach into application/infrastructure/app layers.
    import inspect

    src = inspect.getsource(ct)
    for forbidden in ("app.workers", "contexts.workflow.application", "contexts.workflow.infrastructure"):
        assert forbidden not in src
