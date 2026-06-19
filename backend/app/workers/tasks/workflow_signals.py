"""Arq tasks for workflow signal dispatch and event resume (K.4).

- workflow_signal:         Fan a real-world signal out to parked waits and dormant triggers.
- workflow_variable_signal: Re-check variable_matches waits after a set_variable step.
- workflow_event_resume:   Resume a parked wait_for_event node when its event arrives.
- run_triggered_workflow:  Start a run for a workflow whose dormant trigger matched.
- workflow_event_timeout:  Fire when a wait_for_event node times out.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from loguru import logger

from app.workers.tasks.workflow_common import (
    _RESUME_RETRY_DELAY_S,
    _RESUME_RETRY_MAX_ATTEMPTS,
    _emit_resumed,
    _restore_claim,
    _run_is_terminal,
)


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
