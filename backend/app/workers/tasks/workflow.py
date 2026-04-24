"""Arq tasks for the workflow engine (H.4).

- run_workflow_step:          Resume a workflow run at a specific node.
- retry_workflow_node:        Re-execute a failed node after its retry backoff (W3).
- workflow_event_timeout:     Resume a wait_for_event run at the timeout port (W6).
- workflow_subagent_timeout:  Fail a run that waited too long for a subagent (W10).
- archive_workflow_runs:      Nightly 90-day archive sweep (H.6).
- workflow_cron_scheduler:    Compute next cron fire times and enqueue runs.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def run_workflow_step(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
) -> str:
    """Resume a workflow run at a specific node after unparking."""
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.resume_step(uuid.UUID(run_id), node_id)

    logger.info("workflow step resumed: run=%s node=%s", run_id, node_id)
    return "ok"


async def retry_workflow_node(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
) -> str:
    """W3: Re-execute a failed node after its Arq-delayed backoff period.

    Called instead of asyncio.sleep to avoid holding the DB connection / Arq
    job slot during the retry backoff interval.
    """
    from shared_kernel.db.session import async_session
    from shared_kernel.auth.clients import get_redis

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.retry_node(uuid.UUID(run_id), node_id)
        await db.commit()
        await engine._dispatch_enqueues()

    # Clean up the retry counter after the final retry attempt completes.
    redis = get_redis()
    await redis.delete(f"wf:retry:{run_id}:{node_id}")

    logger.info("workflow node retried: run=%s node=%s", run_id, node_id)
    return "ok"


async def workflow_event_timeout(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
) -> str:
    """W6: Fire when a wait_for_event node times out without receiving its event.

    Checks whether the event arrived (Redis key still present → not yet).
    If timed out, resumes the run at the 'timeout' output port.
    """
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    wait_key = f"wf:wait:{run_id}:{node_id}"

    if not await redis.exists(wait_key):
        # Event already handled by a dispatcher — nothing to do.
        logger.debug("workflow_event_timeout: event already received run=%s node=%s", run_id, node_id)
        return "already_received"

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.resume_at_port(uuid.UUID(run_id), node_id, "timeout")
        await db.commit()
        await engine._dispatch_enqueues()

    # Clean up wait keys.
    info_raw = await redis.get(wait_key)
    if info_raw:
        import json
        try:
            info = json.loads(info_raw)
            event_type = info.get("event_type", "")
            index_key = f"wf:wait:by_event:{event_type}"
            await redis.srem(index_key, f"{run_id}:{node_id}")
        except Exception:
            pass
    await redis.delete(wait_key)

    logger.info("workflow event timed out: run=%s node=%s", run_id, node_id)
    return "timed_out"


async def workflow_subagent_timeout(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
) -> str:
    """W10: Fail the run if a spawned subagent never completes within its timeout."""
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine
        from contexts.workflow.domain.models import RunState

        engine = RunEngine(db)
        run = await engine._runs.get(uuid.UUID(run_id))
        if run and run.state == RunState.WAITING:
            from datetime import datetime, timezone
            logger.warning("subagent timeout: failing run=%s node=%s", run_id, node_id)
            await engine._runs.update_state(
                uuid.UUID(run_id),
                state=RunState.FAILED,
                ended_at=datetime.now(timezone.utc),
            )
            await db.commit()

    return "timed_out"


async def archive_workflow_runs(
    ctx: dict[str, Any],
    cutoff_days: int = 90,
) -> str:
    """Move ended runs older than cutoff_days to workflow_runs_archive.

    Admin-adjustable: if Redis key ``config:archive:cutoff_days`` is set,
    that value overrides the default 90-day cutoff (rate_limit_policies-style
    runtime config pattern).
    """
    import sqlalchemy as sa
    from shared_kernel.db.session import async_session
    from shared_kernel.auth.clients import get_redis
    from contexts.orchestration.infrastructure.tables import workflow_runs
    from contexts.workflow.infrastructure.tables import (
        workflow_runs_archive,
        workflow_steps,
    )

    redis = get_redis()
    override = await redis.get("config:archive:cutoff_days")
    if override:
        try:
            cutoff_days = max(1, int(override))
        except (ValueError, TypeError):
            pass

    cutoff = datetime.now(timezone.utc) - timedelta(days=cutoff_days)
    archived = 0

    async with async_session() as db:
        # Find ended runs older than cutoff
        rows = (
            await db.execute(
                sa.select(workflow_runs).where(
                    sa.and_(
                        workflow_runs.c.ended_at.isnot(None),
                        workflow_runs.c.ended_at < cutoff,
                    ),
                ).limit(500),
            )
        ).all()

        for row in rows:
            # Count steps for summary
            step_count = (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(workflow_steps)
                    .where(workflow_steps.c.run_id == row.id),
                )
            ).scalar() or 0

            failed_steps = (
                await db.execute(
                    sa.select(sa.func.count())
                    .select_from(workflow_steps)
                    .where(
                        sa.and_(
                            workflow_steps.c.run_id == row.id,
                            workflow_steps.c.state == "failed",
                        ),
                    ),
                )
            ).scalar() or 0

            summary = {
                "node_count": step_count,
                "failures": failed_steps,
            }

            # Insert archive row
            await db.execute(
                workflow_runs_archive.insert().values(
                    id=row.id,
                    workflow_id=row.workflow_id,
                    trigger_type=row.trigger_type,
                    started_by_user_id=row.started_by_user_id,
                    state=row.state,
                    started_at=row.started_at,
                    ended_at=row.ended_at,
                    summary=summary,
                ),
            )

            # Delete steps
            await db.execute(
                workflow_steps.delete().where(workflow_steps.c.run_id == row.id),
            )

            # Delete the run
            await db.execute(
                workflow_runs.delete().where(workflow_runs.c.id == row.id),
            )

            archived += 1

        await db.commit()

    logger.info("archived %d workflow runs (cutoff=%dd)", archived, cutoff_days)
    return f"archived={archived}"


async def workflow_cron_scheduler(ctx: dict[str, Any]) -> str:
    """Compute next cron fire times and enqueue runs for due workflows."""
    import sqlalchemy as sa
    from shared_kernel.db.session import async_session
    from shared_kernel.auth.clients import get_redis
    from contexts.workflow.infrastructure.tables import workflows

    now = datetime.now(timezone.utc)
    fired = 0

    async with async_session() as db:
        rows = (
            await db.execute(
                sa.select(workflows).where(workflows.c.deleted_at.is_(None)),
            )
        ).all()

        redis = get_redis()

        for row in rows:
            defn = row.definition or {}
            nodes = defn.get("nodes", [])
            trigger_nodes = [n for n in nodes if n.get("type") == "trigger"]

            for trigger in trigger_nodes:
                config = trigger.get("config", {})
                if config.get("trigger_type") != "cron":
                    continue

                cron_expr = config.get("cron_expression", "")
                if not cron_expr:
                    continue

                redis_key = f"wf:cron:{row.id}:last_fire"
                last_fire = await redis.get(redis_key)

                if last_fire:
                    last_dt = datetime.fromisoformat(last_fire.decode())
                    if (now - last_dt).total_seconds() < 60:
                        continue

                try:
                    from croniter import croniter  # type: ignore[import-untyped]
                    tz_str = config.get("timezone", "UTC")
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo(tz_str)
                    base = (
                        datetime.fromisoformat(last_fire.decode())
                        if last_fire
                        else now - timedelta(minutes=1)
                    )
                    cron_it = croniter(cron_expr, base.astimezone(tz))
                    next_fire = cron_it.get_next(datetime)

                    if next_fire <= now.astimezone(tz):
                        from contexts.workflow.application.workflow_service import WorkflowService
                        svc = WorkflowService(db)
                        await svc.trigger_run(
                            row.id,
                            trigger_payload={"trigger_type": "cron"},
                        )
                        await db.commit()
                        await redis.set(redis_key, now.isoformat(), ex=86400)
                        fired += 1
                except Exception:
                    logger.exception("cron eval failed for workflow %s", row.id)

    logger.info("cron scheduler: fired %d workflows", fired)
    return f"fired={fired}"
