"""Arq tasks for the workflow engine (H.4).

- run_workflow_step:          Resume a workflow run at a specific node.
- retry_workflow_node:        Re-execute a failed node after its retry backoff (W3).
- workflow_event_timeout:     Resume a wait_for_event run at the timeout port (W6).
- workflow_subagent_timeout:  Fail a run that waited too long for a subagent (W10).
- workflow_subagent_complete: Resume a run whose spawned subagent finished (W10).
- workflow_cron_scheduler:    Compute next cron fire times and enqueue runs.

Nightly 90-day run archival now lives in ``retention.retention_sweep``
(see its ``_archive_workflow_runs`` policy) — a single retention path per
table replaces the former duplicate ``archive_workflow_runs`` cron.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger


async def run_workflow_step(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
    from_edge: str | None = None,
) -> str:
    """Execute one parallel fan-out branch node, then follow its edges (W1).

    The engine's parallel fan-out enqueues one of these per outgoing edge. The
    task must *run* ``node_id`` itself — ``RunEngine.run_step`` does that —
    rather than advance past it (the earlier ``resume_step`` call skipped the
    branch's first node entirely). ``from_edge`` is the spawning edge id (ASYNC-9).
    """
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.run_step(uuid.UUID(run_id), node_id, from_edge=from_edge)
        # DB-1: the task owns the transaction — commit once, then dispatch.
        await db.commit()
        # ASYNC-6: reuse this worker's own Arq pool — never open a fresh one.
        await engine.dispatch_enqueues(ctx["redis"])

    logger.bind(event="workflow_branch_executed", run_id=run_id, node_id=node_id).info(
        "workflow branch executed"
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
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.retry_node(uuid.UUID(run_id), node_id)
        await db.commit()
        await engine.dispatch_enqueues(ctx["redis"])  # ASYNC-6: reuse worker pool

    # Clean up the retry counter after the final retry attempt completes.
    redis = get_redis()
    await redis.delete(f"wf:retry:{run_id}:{node_id}")

    logger.bind(event="workflow_node_retried", run_id=run_id, node_id=node_id).info("workflow node retried")
    return "ok"


async def workflow_event_timeout(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
) -> str:
    """W6: Fire when a wait_for_event node times out without receiving its event.

    ASYNC-10: ownership of the resume is claimed atomically with ``GETDEL`` on
    ``wf:wait:{run_id}:{node_id}``. Whoever deletes the key — this timeout job,
    an Arq re-delivery of it, or an event dispatcher — owns the single resume;
    every other party sees ``None`` and stops. This closes the TOCTOU window
    where a plain ``EXISTS`` check let a run resume twice (once at ``timeout``
    and once at ``default``), spawning duplicate downstream steps.
    """
    import json

    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    wait_key = f"wf:wait:{run_id}:{node_id}"

    # Atomic claim: GETDEL hands the payload to exactly one caller and removes
    # the key in the same step. A loser (event already dispatched, or this is a
    # duplicate job) gets None and must not resume.
    claimed = await redis.getdel(wait_key)
    if claimed is None:
        logger.bind(run_id=run_id, node_id=node_id).debug(
            "workflow_event_timeout: wait already claimed (event received or duplicate job)"
        )
        return "already_received"

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.resume_at_port(uuid.UUID(run_id), node_id, "timeout")
        await db.commit()
        # ASYNC-6: reuse this worker's own Arq pool — never open a fresh one.
        await engine.dispatch_enqueues(ctx["redis"])

    # Best-effort cleanup of the by-event index, using the claimed payload.
    try:
        info = json.loads(claimed)
        event_type = info.get("event_type", "")
        index_key = f"wf:wait:by_event:{event_type}"
        await redis.srem(index_key, f"{run_id}:{node_id}")
    except Exception:
        # Index cleanup is best-effort — the wait_key is already gone. Surface a
        # malformed payload so it is noticed, but do NOT abort the timeout flow.
        logger.bind(run_id=run_id, node_id=node_id).exception(
            "workflow_event_timeout: failed to remove from event index"
        )

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
            from datetime import datetime

            logger.bind(run_id=run_id, node_id=node_id).warning("subagent timeout: failing run")
            await engine._runs.update_state(
                uuid.UUID(run_id),
                state=RunState.FAILED,
                ended_at=datetime.now(UTC),
            )
            await db.commit()

    return "timed_out"


async def workflow_subagent_complete(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
    port: str = "success",
) -> str:
    """W10: Resume a run parked on ``subagent_spawn`` once its subagent finishes.

    Enqueued by ``SubagentService.destroy`` when a spawned agent instance that
    carries a ``wf:subagent_callback:{instance_id}`` marker is torn down. The
    callback payload supplies ``run_id`` / ``node_id`` / ``port``; this task
    drives the workflow engine to resume from that output port.
    """
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.resume_at_port(uuid.UUID(run_id), node_id, port)
        await db.commit()
        await engine.dispatch_enqueues(ctx["redis"])  # ASYNC-6: reuse worker pool

    logger.bind(event="workflow_subagent_resumed", run_id=run_id, node_id=node_id, port=port).info(
        "workflow run resumed after subagent completion"
    )
    return "resumed"


async def workflow_cron_scheduler(ctx: dict[str, Any]) -> str:
    """Compute next cron fire times and enqueue runs for due workflows."""
    import sqlalchemy as sa

    from contexts.workflow.infrastructure.tables import workflows
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    now = datetime.now(UTC)
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
                    last_dt = datetime.fromisoformat(last_fire)
                    if (now - last_dt).total_seconds() < 60:
                        continue

                eligible += 1
                try:
                    from croniter import croniter  # type: ignore[import-untyped]

                    tz_str = config.get("timezone", "UTC")
                    import zoneinfo

                    tz = zoneinfo.ZoneInfo(tz_str)
                    base = (
                        datetime.fromisoformat(last_fire)
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
                        await svc.dispatch_pending(ctx["redis"])  # ASYNC-6
                        await redis.set(redis_key, now.isoformat(), ex=86400)
                        fired += 1
                except Exception as exc:
                    logger.bind(workflow_id=str(row.id)).exception("cron eval failed")
                    errors.append(f"{row.id}: {exc}")

    logger.bind(event="cron_scheduler_done", fired=fired, errors=len(errors)).info(
        f"cron scheduler: fired {fired} workflows"
    )
    # If every eligible workflow blew up, surface the failure to Arq so the
    # job is retried / alerted on, instead of silently reporting success.
    if errors and eligible > 0 and len(errors) == eligible:
        raise RuntimeError(
            f"cron scheduler: all {eligible} eligible workflows failed: " + "; ".join(errors),
        )
    return f"fired={fired} errors={len(errors)}"
