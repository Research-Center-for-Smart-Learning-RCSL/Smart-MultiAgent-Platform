"""Arq task for workflow timeout watchdog (K.4).

- workflow_watchdog: Fail runs that blow their run_max_seconds / idle_max_seconds.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger


async def _cleanup_dangling_wait_index(redis: Any, run_id: uuid.UUID, definition: dict[str, Any]) -> None:
    """Remove a force-failed run's wait_for_event nodes from every by-event
    index they might still be a member of (code-review finding).

    F-37 correctly stopped ``find_matching_waits`` pruning on a transiently
    absent claim key — that absence can mean another task's in-flight claim
    window, not permanent staleness. But a claim key can also be lost for
    good (a worker crash between its GETDEL and the resume/restore commit),
    in which case *neither* the event path nor the timeout path can ever
    claim it again, and the run eventually lands here, force-failed for
    idleness. That is the one signal DB-authoritative enough to prune
    safely: a run this function just confirmed terminal cannot legitimately
    still be waiting on anything. SREM of a non-member is a no-op, so it's
    safe to sweep every wait_for_event node in the definition without first
    determining which one was actually parked.
    """
    for node in definition.get("nodes", []):
        if node.get("type") != "wait_for_event":
            continue
        node_id = node.get("id", "")
        event_type = node.get("config", {}).get("event_type", "")
        if not node_id or not event_type:
            continue
        try:
            await redis.srem(f"wf:wait:by_event:{event_type}", f"{run_id}:{node_id}")
        except Exception:
            logger.bind(run_id=str(run_id), node_id=node_id).exception(
                "watchdog: failed to clean up wait index for force-failed run"
            )


async def workflow_watchdog(ctx: dict[str, Any]) -> str:
    """Fail runs that blow their ``run_max_seconds`` / ``idle_max_seconds`` (K.4).

    Cron-driven. The engine enforces neither budget today — a run parked on a
    wait whose event never arrives (and whose timeout job was lost) would sit
    RUNNING/WAITING forever. This sweep is the backstop: per active run it loads
    the workflow definition's timeouts and fails the run past either budget.

    Idle-vs-parked interaction: the idle clock is the latest step ``started_at``
    (``latest_activity_at``), and a *legitimately* parked run (approval_gate /
    wait_for_event / instruct) accrues idle time the whole while it waits. The
    defaults live in ``RunContext`` (``run_max_seconds=3600``,
    ``idle_max_seconds=1800``, override via the definition's ``timeouts``
    block); a workflow whose longest gate/wait timeout exceeds
    ``idle_max_seconds`` WILL be force-failed by this watchdog while merely
    waiting — authors must set ``idle_max_seconds`` above their longest
    gate/wait timeout.
    """
    from contexts.workflow.application.run_engine import RunEngine
    from contexts.workflow.domain.models import RunContext
    from contexts.workflow.infrastructure.repositories import (
        WorkflowRepository,
        WorkflowRunRepository,
        WorkflowStepRepository,
    )
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    now = datetime.now(UTC)
    failed = 0
    checked = 0

    async with async_session() as db:
        runs = WorkflowRunRepository(db)
        steps = WorkflowStepRepository(db)
        workflows_repo = WorkflowRepository(db)
        active = await runs.list_active()

        for run_id, workflow_id, started_at in active:
            checked += 1
            try:
                wf = await workflows_repo.get(workflow_id, include_deleted=True)
                if wf is None:
                    continue
                ctx_view = RunContext(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    workflow_def=wf.definition,
                    variables={},
                )
                run_age = (now - started_at).total_seconds()
                reason: str | None = None
                if run_age > ctx_view.run_max_seconds:
                    reason = f"run_max_seconds exceeded ({run_age:.0f}s > {ctx_view.run_max_seconds}s)"
                else:
                    last = await steps.latest_activity_at(run_id)
                    idle_since = last or started_at
                    idle = (now - idle_since).total_seconds()
                    if idle > ctx_view.idle_max_seconds:
                        reason = f"idle_max_seconds exceeded ({idle:.0f}s > {ctx_view.idle_max_seconds}s)"
                if reason is None:
                    continue
                engine = RunEngine(db)
                if await engine.force_fail(run_id, reason=reason):
                    await db.commit()
                    await engine.dispatch_enqueues(ctx.get("redis"))
                    await _cleanup_dangling_wait_index(get_redis(), run_id, wf.definition)
                    failed += 1
            except Exception:
                await db.rollback()
                logger.bind(run_id=str(run_id)).exception("watchdog: run check failed")

        redis = ctx.get("redis")
        if redis is not None:
            for run_id in await runs.list_a2a_cancellation_pending():
                await redis.enqueue_job("workflow_cancel_a2a_calls", str(run_id), 0)

    logger.bind(event="workflow_watchdog_done", checked=checked, failed=failed).info(
        f"workflow watchdog: failed {failed}/{checked} active runs"
    )
    return f"failed={failed}"
