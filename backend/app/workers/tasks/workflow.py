"""Arq tasks for the workflow engine (H.4).

- run_workflow_step:          Resume a workflow run at a specific node.
- retry_workflow_node:        Re-execute a failed node after its retry backoff (W3).
- workflow_event_timeout:     Resume a wait_for_event run at the timeout port (W6).
- workflow_subagent_timeout:  Fail a run that waited too long for a subagent (W10).
- archive_workflow_runs:      Nightly 90-day archive sweep (H.6).
- workflow_cron_scheduler:    Compute next cron fire times and enqueue runs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger


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

    logger.bind(event="workflow_step_resumed", run_id=run_id, node_id=node_id).info(
        "workflow step resumed"
    )
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

    logger.bind(event="workflow_node_retried", run_id=run_id, node_id=node_id).info(
        "workflow node retried"
    )
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
        logger.bind(run_id=run_id, node_id=node_id).debug(
            "workflow_event_timeout: event already received"
        )
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
            # Index cleanup is best-effort — the wait_key itself is dropped below
            # regardless. Surface the parse failure so a malformed payload is
            # noticed, but do NOT abort the timeout flow.
            logger.bind(run_id=run_id, node_id=node_id).exception(
                "workflow_event_timeout: failed to remove from event index"
            )
    await redis.delete(wait_key)

    logger.bind(event="workflow_event_timed_out", run_id=run_id, node_id=node_id).info(
        "workflow event timed out"
    )
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
            logger.bind(run_id=run_id, node_id=node_id).warning(
                "subagent timeout: failing run"
            )
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
        except (ValueError, TypeError) as exc:
            # Bad override silently degrading to the default has masked
            # operator typos in the past — log loud, keep the default.
            logger.bind(override=override, error=str(exc)).warning(
                "archive_workflow_runs: invalid config:archive:cutoff_days override; "
                f"falling back to default {cutoff_days}d"
            )

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

    logger.bind(event="workflow_archive_done", archived=archived, cutoff_days=cutoff_days).info(
        f"archived {archived} workflow runs (cutoff={cutoff_days}d)"
    )
    return f"archived={archived}"


async def workflow_cron_scheduler(ctx: dict[str, Any]) -> str:
    """Compute next cron fire times and enqueue runs for due workflows."""
    import sqlalchemy as sa
    from shared_kernel.db.session import async_session
    from shared_kernel.auth.clients import get_redis
    from contexts.workflow.infrastructure.tables import workflows

    now = datetime.now(timezone.utc)
    fired = 0
    eligible = 0
    errors: list[str] = []

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

                eligible += 1
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
                except Exception as exc:
                    logger.bind(workflow_id=str(row.id)).exception(
                        "cron eval failed"
                    )
                    errors.append(f"{row.id}: {exc}")

    logger.bind(event="cron_scheduler_done", fired=fired, errors=len(errors)).info(
        f"cron scheduler: fired {fired} workflows"
    )
    # If every eligible workflow blew up, surface the failure to Arq so the
    # job is retried / alerted on, instead of silently reporting success.
    if errors and eligible > 0 and len(errors) == eligible:
        raise RuntimeError(
            f"cron scheduler: all {eligible} eligible workflows failed: "
            + "; ".join(errors),
        )
    return f"fired={fired} errors={len(errors)}"
