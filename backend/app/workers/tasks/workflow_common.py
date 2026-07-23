"""Shared helpers for workflow task sub-modules.

Extracted from the monolithic ``workflow.py`` to support the SoC split into
workflow_steps / workflow_cron / workflow_signals / workflow_approvals /
workflow_watchdog.
"""

from __future__ import annotations

import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Claim-before-verify recovery (K remediation)
#
# Every resume task claims its single-shot token (``wf:wait:*`` /
# ``wf:approval:*`` / ``wf:instruct:*``) with GETDEL *before* calling
# ``RunEngine.resume_at_port``, which silently no-ops when the run is not
# WAITING (the parking transaction hasn't committed yet — the claim key is
# written inside it but visible to Redis immediately — or a parallel sibling
# branch holds the run in RUNNING). Dropping the claim there lost the wait
# forever. On a failed resume of a NON-terminal run the claim is restored with
# its remaining TTL and the task re-enqueues itself with a short defer, bounded
# by the same budget as the approval pending-poll (3 s × 210 ≈ 10.5 min).
# ---------------------------------------------------------------------------

_RESUME_RETRY_DELAY_S = 3
_RESUME_RETRY_MAX_ATTEMPTS = 210
# Floor a restored/extended claim key must never fall below: the *full* consumer
# retry budget. A claim that expires inside its own consumer's budget loses the
# resume silently (F-32); the previous 60 s fallback was shorter than the 630 s
# budget it guarded, which was the bug in miniature. Callers with a decaying
# live budget pass a tighter ``min_ttl`` via ``_remaining_budget_ttl``.
_CLAIM_RESTORE_TTL_S = _RESUME_RETRY_MAX_ATTEMPTS * _RESUME_RETRY_DELAY_S


def _remaining_budget_ttl(max_attempts: int, delay_s: int, attempt: int) -> int:
    """Seconds a claim key must still live to outlast a consumer's remaining
    retry budget (F-32). One extra delay cycle of margin covers the gap between
    extending the key and the next retry actually running."""
    return max(0, max_attempts - attempt + 1) * delay_s


async def _run_is_terminal(db: Any, run_id: str) -> bool:
    """True when the run is gone or in a terminal state (no resume possible)."""
    from contexts.workflow.infrastructure.repositories import WorkflowRunRepository

    run = await WorkflowRunRepository(db).get(uuid.UUID(run_id))
    return run is None or run.state.is_terminal


async def _restore_claim(
    redis: Any,
    key: str,
    payload: Any,
    ttl: int | None,
    *,
    min_ttl: int = _CLAIM_RESTORE_TTL_S,
) -> None:
    """Put a GETDEL-claimed resume token back so a later claimant can own it.

    Restores with the larger of the key's remaining TTL and ``min_ttl`` so the
    key never expires inside the consumer's remaining retry budget (F-32).
    ``min_ttl`` defaults to the full budget; callers pass their decaying
    remaining budget via :func:`_remaining_budget_ttl`.
    """
    await redis.set(key, payload, ex=max(ttl or 0, min_ttl))


async def _emit_resumed(db: Any, run_id: str, node_id: str, *, reason: str) -> None:
    """Audit ``workflow.resumed`` (cross-cutting checklist item 2)."""
    from shared_kernel import audit

    await audit.emit(
        db,
        audit.AuditEvent(
            action="workflow.resumed",
            resource_type="workflow_run",
            resource_id=uuid.UUID(run_id),
            metadata={"node_id": node_id, "reason": reason},
        ),
    )
