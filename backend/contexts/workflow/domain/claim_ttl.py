"""Single source of truth for workflow resume claim-key TTLs (FU-3).

A resume claim key (``wf:approval`` / ``wf:instruct`` / ``wf:wait`` /
``wf:subagent_callback``) is written by a producer executor at park time and read
by a consumer worker task when the node resolves. Two lifetimes must stay
reconciled, and this module is the one place that states the relationship so a
future change to either side cannot silently break it:

- **I1 (producer grace).** The initial key TTL is ``timeout_seconds + grace``. The
  grace only has to cover the window between key creation and the *first* consumer
  action after resolution (the resume/timeout job being enqueued and run) — not the
  whole consumer budget. Producers pass :data:`GATE_CLAIM_GRACE_S` or
  :data:`WAIT_CLAIM_GRACE_S` to :func:`initial_claim_ttl`.

- **I2 (consumer extension).** Once a consumer begins retrying (run not yet
  ``WAITING``, resolver's transaction not yet committed, or a parallel sibling
  holding the run), it must keep the key alive for at least its remaining retry
  budget, or the claim expires mid-retry and the resume is lost. The consumer
  enforces this via :func:`remaining_budget_ttl` and ``_restore_claim`` /
  ``EXPIRE`` (the F-32 fix, ``2026-07-22-approval-resume-claim-reliability``).

Because of I2, the producer grace does **not** need to equal or exceed the
consumer budget — that was the pre-F-32 reading. The invariant the two sides now
share is simply: *the key must never be unreachable while a consumer still intends
to act on it*, split into the creation window (I1) and the retry window (I2).

SoC note: this lives in ``domain`` (pure Python, no intra-project imports) so both
the producer executors (``contexts/workflow/application/executors/*``) and the
consumer worker tasks (``app/workers/tasks/*``, an upper layer) can import it
without an upward dependency.
"""

from __future__ import annotations

# --- Consumer retry budget --------------------------------------------------
# A resume task re-enqueues itself up to MAX_ATTEMPTS times, DELAY seconds apart.
# The budget is that product; the consumer floors restored/extended key TTLs to
# (the remaining slice of) it so the key outlives the retries (I2).
CLAIM_RESUME_DELAY_S = 3
CLAIM_RESUME_MAX_ATTEMPTS = 210
CLAIM_CONSUMER_BUDGET_S = CLAIM_RESUME_MAX_ATTEMPTS * CLAIM_RESUME_DELAY_S  # 630

# --- Producer creation grace (I1) -------------------------------------------
# Added to the node's own timeout_seconds when the claim key is first written.
GATE_CLAIM_GRACE_S = 300  # approval_gate, instruct
WAIT_CLAIM_GRACE_S = 60  # wait_for_event (claim key + by-event index)
# NB: subagent_spawn was a third user of this grace until
# 2026-07-22-subagent-spawn-fail-fast removed its park. It never had a claim/restore/
# retry consumer, which is why it used the value for shape parity only.


def initial_claim_ttl(timeout_seconds: int, grace_s: int) -> int:
    """Initial TTL a producer writes for a resume claim key: node timeout + grace (I1)."""
    return int(timeout_seconds) + grace_s


def remaining_budget_ttl(max_attempts: int, delay_s: int, attempt: int) -> int:
    """Seconds a claim key must still live to outlast a consumer's remaining retry
    budget (I2). One extra delay cycle of margin covers the gap between extending
    the key and the next retry actually running."""
    return max(0, max_attempts - attempt + 1) * delay_s


__all__ = [
    "CLAIM_CONSUMER_BUDGET_S",
    "CLAIM_RESUME_DELAY_S",
    "CLAIM_RESUME_MAX_ATTEMPTS",
    "GATE_CLAIM_GRACE_S",
    "WAIT_CLAIM_GRACE_S",
    "initial_claim_ttl",
    "remaining_budget_ttl",
]
