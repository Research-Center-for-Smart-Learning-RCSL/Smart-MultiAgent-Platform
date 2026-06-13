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
    attempt: int = 0,
) -> str:
    """W6: Fire when a wait_for_event node times out without receiving its event.

    ASYNC-10: ownership of the resume is claimed atomically with ``GETDEL`` on
    ``wf:wait:{run_id}:{node_id}``. Whoever deletes the key — this timeout job,
    an Arq re-delivery of it, or an event dispatcher — owns the single resume;
    every other party sees ``None`` and stops. This closes the TOCTOU window
    where a plain ``EXISTS`` check let a run resume twice (once at ``timeout``
    and once at ``default``), spawning duplicate downstream steps.

    Claim-before-verify: if the run turns out not to be WAITING (parallel
    sibling running), the claim is restored and this job retries bounded.
    """
    import json

    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    wait_key = f"wf:wait:{run_id}:{node_id}"

    # Capture the remaining TTL before claiming so a failed resume can restore
    # the claim with (approximately) its original deadline.
    ttl = await redis.ttl(wait_key)
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
        resumed = await engine.resume_at_port(uuid.UUID(run_id), node_id, "timeout")
        await db.commit()
        if not resumed and not await _run_is_terminal(db, run_id):
            # Run not WAITING (parallel sibling executing) — restore the claim
            # and retry; the wait must not be lost (claim-before-verify).
            await _restore_claim(redis, wait_key, claimed, ttl)
            if attempt < _RESUME_RETRY_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "workflow_event_timeout",
                    run_id,
                    node_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_RESUME_RETRY_DELAY_S),
                )
                return "not_waiting:retry"
            return "not_waiting:gave_up"
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

    if not resumed:
        return "noop:terminal"
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


# ===========================================================================
# K.4 — signal dispatch (resume parked waits + start dormant trigger kinds)
# ===========================================================================


async def workflow_signal(ctx: dict[str, Any], source: str, payload: dict[str, Any]) -> str:
    """Fan a real-world signal out to parked waits and dormant trigger nodes (K.4).

    Enqueued (best-effort, post-commit) from the three severed link points:
    message send (``source="message"``), the A2A consumer (``"a2a"``), and the
    wake-up path (``"wakeup"``). (The intra-run ``variable_matches`` re-check has
    its own engine-driven task, ``workflow_variable_signal``.) Itself a
    lightweight fan-out — it never resumes or triggers inline; it enqueues one
    ``workflow_event_resume`` per matching wait and one ``run_triggered_workflow``
    per matching dormant trigger, so each resumed/started run owns its session,
    commit and dispatch.
    """
    from contexts.workflow.application import event_dispatch as ed
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    pool = ctx["redis"]
    resumed = 0
    triggered = 0

    async def _enqueue_resume(run_id: str, node_id: str) -> None:
        nonlocal resumed
        await pool.enqueue_job("workflow_event_resume", run_id, node_id)
        resumed += 1

    async def _enqueue_triggers(wf_ids: list[Any], trigger_type: str) -> None:
        nonlocal triggered
        for wf_id in wf_ids:
            tp = {"trigger_type": trigger_type, **payload}
            await pool.enqueue_job("run_triggered_workflow", str(wf_id), tp)
            triggered += 1

    if source == "message":
        chatroom_id = str(payload.get("chatroom_id", ""))
        sender_type = str(payload.get("sender_type", ""))
        content = str(payload.get("content", ""))

        def _wait_pred(match: dict[str, Any]) -> bool:
            return ed.matches_message(
                match, chatroom_id=chatroom_id, sender_type=sender_type, content=content
            )

        for run_id, node_id in await ed.find_matching_waits(redis, "message_in_room", _wait_pred):
            await _enqueue_resume(run_id, node_id)

        def _trig_pred(config: dict[str, Any]) -> bool:
            return ed.matches_message(
                config, chatroom_id=chatroom_id, sender_type=sender_type, content=content
            )

        async with async_session() as db:
            wf_ids = await ed.find_triggered_workflows(db, "message_received", _trig_pred)
        await _enqueue_triggers(wf_ids, "message_received")

    elif source == "a2a":
        target_agent_id = str(payload.get("target_agent_id", ""))
        msg_type = str(payload.get("msg_type", ""))

        def _a2a_wait_pred(match: dict[str, Any]) -> bool:
            return ed.matches_a2a(match, target_agent_id=target_agent_id, msg_type=msg_type)

        for run_id, node_id in await ed.find_matching_waits(redis, "a2a_message", _a2a_wait_pred):
            await _enqueue_resume(run_id, node_id)

        def _a2a_trig_pred(config: dict[str, Any]) -> bool:
            return ed.matches_a2a_trigger(config, agent_id=target_agent_id, msg_type=msg_type)

        async with async_session() as db:
            wf_ids = await ed.find_triggered_workflows(db, "a2a_event", _a2a_trig_pred)
        await _enqueue_triggers(wf_ids, "a2a_event")

    elif source == "wakeup":
        agent_id = str(payload.get("agent_id", ""))

        def _wake_pred(config: dict[str, Any]) -> bool:
            return str(config.get("agent_id", "")) == agent_id

        async with async_session() as db:
            wf_ids = await ed.find_triggered_workflows(db, "wakeup_signal", _wake_pred)
        await _enqueue_triggers(wf_ids, "wakeup_signal")

    logger.bind(event="workflow_signal", source=source, resumed=resumed, triggered=triggered).info(
        "workflow signal dispatched"
    )
    return f"resumed={resumed} triggered={triggered}"


async def workflow_variable_signal(ctx: dict[str, Any], run_id: str, node_id: str) -> str:
    """Re-check variable_matches waits in a run after a set_variable step (K.4).

    Enqueued by the engine post-commit (so the new variables are visible). The
    ``node_id`` is the set_variable node that fired; it is logged only — the
    re-check spans every variable_matches wait parked in this run.
    """
    from contexts.workflow.application import event_dispatch as ed
    from contexts.workflow.infrastructure.repositories import WorkflowRunRepository
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    async with async_session() as db:
        run = await WorkflowRunRepository(db).get(uuid.UUID(run_id))
        variables = dict(run.variables) if run else {}

    resumed = 0
    for rid, nid, match in await ed.find_run_variable_waits(redis, run_id):
        if ed.matches_variable(match, variables):
            await ctx["redis"].enqueue_job("workflow_event_resume", rid, nid)
            resumed += 1

    logger.bind(run_id=run_id, node_id=node_id, resumed=resumed).debug("variable signal dispatched")
    return f"resumed={resumed}"


async def workflow_event_resume(ctx: dict[str, Any], run_id: str, node_id: str, attempt: int = 0) -> str:
    """Resume a parked ``wait_for_event`` node when its event arrives (K.4).

    ASYNC-10: the resume is claimed atomically with ``GETDEL`` on
    ``wf:wait:{run_id}:{node_id}`` — the same key the timeout job claims — so an
    event and its timeout can never both resume the run.

    Claim-before-verify: when ``resume_at_port`` reports the run was not
    WAITING (park not yet committed, or a parallel sibling holds the run in
    RUNNING) and the run is not terminal, the claim is restored with its
    remaining TTL and this job retries bounded — otherwise the wait was lost.
    """
    import json

    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    wait_key = f"wf:wait:{run_id}:{node_id}"
    ttl = await redis.ttl(wait_key)
    claimed = await redis.getdel(wait_key)
    if claimed is None:
        return "already_claimed"

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        resumed = await engine.resume_at_port(uuid.UUID(run_id), node_id, "default")
        if not resumed:
            await db.commit()  # persist side effects (e.g. workflow-deleted FAILED)
            if not await _run_is_terminal(db, run_id):
                await _restore_claim(redis, wait_key, claimed, ttl)
                if attempt < _RESUME_RETRY_MAX_ATTEMPTS:
                    await ctx["redis"].enqueue_job(
                        "workflow_event_resume",
                        run_id,
                        node_id,
                        attempt + 1,
                        _defer_by=timedelta(seconds=_RESUME_RETRY_DELAY_S),
                    )
                    return "not_waiting:retry"
                return "not_waiting:gave_up"
            # Terminal run: drop the claim and fall through to index cleanup.
        else:
            await _emit_resumed(db, run_id, node_id, reason="event")
            await db.commit()
            await engine.dispatch_enqueues(ctx["redis"])

    # Best-effort index cleanup using the claimed payload.
    try:
        info = json.loads(claimed)
        event_type = info.get("event_type", "")
        await redis.srem(f"wf:wait:by_event:{event_type}", f"{run_id}:{node_id}")
    except Exception:
        logger.bind(run_id=run_id, node_id=node_id).exception("event resume: index cleanup failed")

    if not resumed:
        return "noop:terminal"
    logger.bind(event="workflow_event_resumed", run_id=run_id, node_id=node_id).info("workflow event resumed")
    return "resumed"


async def run_triggered_workflow(
    ctx: dict[str, Any],
    workflow_id: str,
    trigger_payload: dict[str, Any] | None = None,
) -> str:
    """Start a run for a workflow whose dormant trigger node matched a signal (K.4)."""
    from contexts.workflow.application.workflow_service import WorkflowService
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        svc = WorkflowService(db)
        try:
            run_id = await svc.trigger_run(uuid.UUID(workflow_id), trigger_payload=trigger_payload or {})
        except Exception:
            logger.bind(workflow_id=workflow_id).exception("triggered workflow start failed")
            return "error"
        await db.commit()
        await svc.dispatch_pending(ctx["redis"])

    logger.bind(event="workflow_triggered", workflow_id=workflow_id, run_id=str(run_id)).info(
        "dormant-trigger workflow started"
    )
    return str(run_id)


# ===========================================================================
# K.4 — approval / instruct resume + timeout watchdog
# ===========================================================================

# Poll budget bridging the gap between an approval resolving inside an agent
# turn's tool call and that turn's single end-of-turn commit (the turn engine
# commits once). 3 s × 210 ≈ 10.5 min ≥ the 600 s job timeout, so any voting
# turn has committed (or rolled back) before the budget is spent.
_APPROVAL_RESUME_DELAY_S = 3
_APPROVAL_RESUME_MAX_ATTEMPTS = 210


async def workflow_resume_approval(ctx: dict[str, Any], approval_id: str, attempt: int = 0) -> str:
    """Resume a workflow run parked on ``approval_gate`` once the gate resolves (K.4).

    Enqueued by ``ApprovalService`` on vote-driven and timeout-driven resolution.
    A vote resolves the gate inside the voting agent's turn, which only commits
    at turn end, so this job re-checks (bounded) until the resolved state is
    visible, then atomically claims ``wf:approval:{id}`` and resumes at the
    approved/rejected/timeout port. Non-workflow (room-only) approvals carry no
    claim key and no-op here.
    """
    import json

    from contexts.orchestration.domain.models import ApprovalState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    key = f"wf:approval:{approval_id}"
    if await redis.get(key) is None:
        return "noop:no_claim"

    aid = uuid.UUID(approval_id)
    async with async_session() as db:
        facade = OrchestrationFacade(db)
        approval = await facade.get_approval(aid)
        if approval is None:
            await redis.delete(key)
            return "noop:gone"
        if approval.state == ApprovalState.PENDING:
            # Resolver's transaction not yet committed (long turn) or it rolled
            # back. Retry within budget; the gate-timeout path will resolve and
            # re-enqueue if votes never commit.
            if attempt < _APPROVAL_RESUME_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "workflow_resume_approval",
                    approval_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_APPROVAL_RESUME_DELAY_S),
                )
            return "pending:retry"

        votes = await facade.get_approval_votes(aid)
        port = _approval_port(approval, votes)

        ttl = await redis.ttl(key)
        claimed = await redis.getdel(key)
        if claimed is None:
            return "noop:claimed_elsewhere"
        info = json.loads(claimed)

        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        resumed = await engine.resume_at_port(uuid.UUID(info["run_id"]), info["node_id"], port)
        if not resumed:
            await db.commit()  # persist side effects (e.g. workflow-deleted FAILED)
            if await _run_is_terminal(db, info["run_id"]):
                return "noop:terminal"
            # Claim-before-verify: run not WAITING yet (parking commit pending
            # or a parallel sibling running) — restore the claim and retry.
            # Shares the attempt budget with the pending-poll above.
            await _restore_claim(redis, key, claimed, ttl)
            if attempt < _APPROVAL_RESUME_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "workflow_resume_approval",
                    approval_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_APPROVAL_RESUME_DELAY_S),
                )
                return "not_waiting:retry"
            return "not_waiting:gave_up"
        await _emit_resumed(db, info["run_id"], info["node_id"], reason=f"approval:{port}")
        await db.commit()
        await engine.dispatch_enqueues(ctx["redis"])

    logger.bind(event="workflow_approval_resumed", approval_id=approval_id, port=port).info(
        "workflow resumed after approval"
    )
    return f"resumed:{port}"


def _approval_port(approval: Any, votes: list[Any]) -> str:
    """Map a resolved approval to the approval_gate output port."""
    from contexts.orchestration.domain.models import ApprovalState

    if approval.state == ApprovalState.APPROVED:
        return "approved"
    if approval.state == ApprovalState.REJECTED:
        return "rejected"
    # TIMEOUT_LEADER — the leader's last vote breaks it; no leader vote → timeout.
    leader_votes = [v for v in votes if v.voter_agent_id == approval.leader_agent_id]
    if leader_votes:
        return "approved" if leader_votes[-1].vote else "rejected"
    return "timeout"


async def workflow_resume_instruct(ctx: dict[str, Any], instruction_id: str, attempt: int = 0) -> str:
    """Resume a workflow run parked on ``instruct`` once the instruction settles (K.4).

    Enqueued post-commit by the A2A handler (completion) and by
    ``workflow_instruct_timeout`` (deadline). The committed instruction state
    decides the port, so completion and timeout can't disagree; ``GETDEL`` on
    ``wf:instruct:{id}`` makes the resume single-shot. Non-workflow instructs
    carry no claim key and no-op.

    Also populates the instruct node's ``output_variable`` (the executor only
    does so on the non-parked path) and, claim-before-verify, restores the
    claim + retries bounded when the run is not WAITING yet.
    """
    import json

    from contexts.orchestration.domain.models import InstructionState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade
    from shared_kernel.auth.clients import get_redis
    from shared_kernel.db.session import async_session

    redis = get_redis()
    key = f"wf:instruct:{instruction_id}"
    if await redis.get(key) is None:
        return "noop:no_claim"

    iid = uuid.UUID(instruction_id)
    async with async_session() as db:
        instruction = await OrchestrationFacade(db).get_instruction(iid)
        if instruction is None:
            await redis.delete(key)
            return "noop:gone"
        if instruction.state == InstructionState.COMPLETED:
            port = "success"
        elif instruction.state in (InstructionState.TIMEOUT, InstructionState.REJECTED_LOOP):
            port = "failure"
        else:
            return "pending"  # issued/delivered — not settled yet

        ttl = await redis.ttl(key)
        claimed = await redis.getdel(key)
        if claimed is None:
            return "noop:claimed_elsewhere"
        info = json.loads(claimed)

        from contexts.workflow.application.run_engine import RunEngine

        if port == "success":
            # Populate the node's output_variable BEFORE resuming so downstream
            # nodes (and resume_at_port's variable snapshot) see it.
            await _store_instruct_output(db, info["run_id"], info["node_id"], str(iid))

        engine = RunEngine(db)
        resumed = await engine.resume_at_port(uuid.UUID(info["run_id"]), info["node_id"], port)
        if not resumed:
            await db.commit()  # persist side effects (output_variable / failed run)
            if await _run_is_terminal(db, info["run_id"]):
                return "noop:terminal"
            # Claim-before-verify: restore the claim and retry bounded.
            await _restore_claim(redis, key, claimed, ttl)
            if attempt < _RESUME_RETRY_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "workflow_resume_instruct",
                    instruction_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_RESUME_RETRY_DELAY_S),
                )
                return "not_waiting:retry"
            return "not_waiting:gave_up"
        await _emit_resumed(db, info["run_id"], info["node_id"], reason=f"instruct:{port}")
        await db.commit()
        await engine.dispatch_enqueues(ctx["redis"])

    logger.bind(event="workflow_instruct_resumed", instruction_id=instruction_id, port=port).info(
        "workflow resumed after instruct"
    )
    return f"resumed:{port}"


async def workflow_instruct_timeout(ctx: dict[str, Any], instruction_id: str) -> str:
    """Deadline for a parked ``instruct`` node — mark timeout, then resume (K.4)."""
    from contexts.orchestration.domain.models import InstructionState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade
    from shared_kernel.db.session import async_session

    iid = uuid.UUID(instruction_id)
    async with async_session() as db:
        facade = OrchestrationFacade(db)
        instruction = await facade.get_instruction(iid)
        if instruction is None or instruction.state in (
            InstructionState.COMPLETED,
            InstructionState.TIMEOUT,
            InstructionState.REJECTED_LOOP,
        ):
            return "noop"
        await facade.mark_instruct_timeout(iid)
        await db.commit()

    await ctx["redis"].enqueue_job("workflow_resume_instruct", instruction_id)
    logger.bind(instruction_id=instruction_id).info("instruct deadline: marked timeout")
    return "timed_out"


async def _store_instruct_output(db: Any, run_id: str, node_id: str, instruction_id: str) -> None:
    """Populate the instruct node's ``output_variable`` on the parked path.

    The executor writes it only on the non-parked (``wait_for_completion=False``)
    branch; a parked node resumed here never surfaced anything. The instruction's
    reply *text* is not persisted anywhere (the A2A turn result lives only in
    memory in ``a2a_handler``), so — matching the non-parked path's semantics —
    the instruction id is stored. Best-effort: a population failure must not
    block the resume. Idempotent across resume retries.
    """
    try:
        from contexts.workflow.infrastructure.repositories import (
            WorkflowRepository,
            WorkflowRunRepository,
        )

        runs = WorkflowRunRepository(db)
        run = await runs.get(uuid.UUID(run_id))
        if run is None:
            return
        workflow = await WorkflowRepository(db).get(run.workflow_id, include_deleted=True)
        if workflow is None:
            return
        node = next(
            (n for n in workflow.definition.get("nodes", []) if n.get("id") == node_id),
            None,
        )
        output_variable = (node or {}).get("config", {}).get("output_variable")
        if not output_variable:
            return
        variables = dict(run.variables)
        variables[output_variable] = instruction_id
        await runs.update_variables(uuid.UUID(run_id), variables)
    except Exception:
        logger.bind(run_id=run_id, node_id=node_id).exception(
            "instruct resume: output_variable population failed"
        )


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
