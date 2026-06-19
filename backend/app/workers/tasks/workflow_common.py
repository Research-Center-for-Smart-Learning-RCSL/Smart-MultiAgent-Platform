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
# Fallback TTL when the claim's original TTL could not be read (e.g. it had
# already expired between the TTL read and the GETDEL).
_CLAIM_RESTORE_TTL_S = 60


async def _run_is_terminal(db: Any, run_id: str) -> bool:
    """True when the run is gone or in a terminal state (no resume possible)."""
    from contexts.workflow.domain.models import RunState
    from contexts.workflow.infrastructure.repositories import WorkflowRunRepository

    run = await WorkflowRunRepository(db).get(uuid.UUID(run_id))
    return run is None or run.state in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED)


async def _restore_claim(redis: Any, key: str, payload: Any, ttl: int | None) -> None:
    """Put a GETDEL-claimed resume token back so a later claimant can own it."""
    await redis.set(key, payload, ex=ttl if ttl and ttl > 0 else _CLAIM_RESTORE_TTL_S)


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
