"""Arq task for workflow timeout watchdog (K.4).

- workflow_watchdog: Fail runs that blow their run_max_seconds / idle_max_seconds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger


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
                if await RunEngine(db).force_fail(run_id, reason=reason):
                    await db.commit()
                    failed += 1
            except Exception:
                await db.rollback()
                logger.bind(run_id=str(run_id)).exception("watchdog: run check failed")

    logger.bind(event="workflow_watchdog_done", checked=checked, failed=failed).info(
        f"workflow watchdog: failed {failed}/{checked} active runs"
    )
    return f"failed={failed}"
