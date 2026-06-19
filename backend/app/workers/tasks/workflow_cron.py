"""Arq task for cron-triggered workflow scheduling (H.4).

- workflow_cron_scheduler: Compute next cron fire times and enqueue runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger


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
                sa.select(workflows.c.id, workflows.c.definition).where(
                    workflows.c.deleted_at.is_(None),
                ),
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

                # Catch-up window / one-fire-per-pass semantics:
                # - The scheduler cron runs once a minute; each trigger fires at
                #   most ONCE per pass (the 60 s guard below also debounces a
                #   duplicate pass). Sub-minute cron expressions are therefore
                #   effectively clamped to one run per minute.
                # - On a missed window (worker down), the next pass computes the
                #   next fire from ``last_fire`` (or ``now - 60s`` on first
                #   sight) and enqueues a single catch-up run — never one run
                #   per missed tick.
                # - ``last_fire`` lives in Redis with a 24 h TTL; if it expires
                #   (long outage / flush), the baseline resets to ``now - 60s``,
                #   so at most one stale fire can occur.
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
                    base = datetime.fromisoformat(last_fire) if last_fire else now - timedelta(minutes=1)
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
                    # The session is shared across the whole pass: a failed
                    # trigger_run leaves it pending-rollback and would poison
                    # every subsequent workflow — roll back before continuing
                    # (mirrors workflow_watchdog).
                    await db.rollback()
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
