"""Shared helpers for workflow task sub-modules.

Extracted from the monolithic ``workflow.py`` to support the SoC split into
workflow_steps / workflow_cron / workflow_signals / workflow_approvals /
workflow_watchdog.
"""

from __future__ import annotations

import uuid
from typing import Any

from contexts.workflow.domain.claim_ttl import (
    CLAIM_CONSUMER_BUDGET_S,
    CLAIM_RESUME_DELAY_S,
    CLAIM_RESUME_MAX_ATTEMPTS,
)

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
#
# The budget/grace constants and the ``_remaining_budget_ttl`` helper are owned
# by ``contexts.workflow.domain.claim_ttl`` (the single source of truth stating
# the key-life invariant, FU-3); the local names below are aliases kept for
# readability at the call sites.
# ---------------------------------------------------------------------------

_RESUME_RETRY_DELAY_S = CLAIM_RESUME_DELAY_S
_RESUME_RETRY_MAX_ATTEMPTS = CLAIM_RESUME_MAX_ATTEMPTS
# Floor a restored/extended claim key must never fall below: the *full* consumer
# retry budget. A claim that expires inside its own consumer's budget loses the
# resume silently (F-32). Callers with a decaying live budget pass a tighter
# ``min_ttl`` via ``_remaining_budget_ttl``.
_CLAIM_RESTORE_TTL_S = CLAIM_CONSUMER_BUDGET_S


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
    reindex: tuple[str, str] | None = None,
) -> None:
    """Put a GETDEL-claimed resume token back so a later claimant can own it.

    Restores with the larger of the key's remaining TTL and ``min_ttl`` so the
    key never expires inside the consumer's remaining retry budget (F-32).
    ``min_ttl`` defaults to the full budget; callers pass their decaying
    remaining budget via :func:`_remaining_budget_ttl`.

    ``reindex``, when given as ``(index_key, member)``, re-adds the by-event
    index member alongside the claim key — the restore is otherwise not the
    true inverse of the claim, since only ``wait_for_event.py`` writes that
    member (F-37). The approval/instruct callers have no such index and pass
    nothing.
    """
    await redis.set(key, payload, ex=max(ttl or 0, min_ttl))
    if reindex is not None:
        index_key, member = reindex
        await redis.sadd(index_key, member)
        # SADD on an absent key creates it with no TTL. The index set's TTL
        # must only ever be *extended* (wait_for_event.py), and must not lapse
        # while this claim is still being retried, so floor it to the same
        # budget the claim key was just restored to — otherwise a lapsed
        # index key becomes permanently non-expiring the moment this restore
        # recreates it (F-37).
        index_ttl = await redis.ttl(index_key)
        if index_ttl < min_ttl:
            await redis.expire(index_key, min_ttl)


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
