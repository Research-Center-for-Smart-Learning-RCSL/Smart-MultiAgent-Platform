"""Arq tasks for workflow step execution (H.4 / W1 / W3 / W10).

- run_workflow_step:          Execute one parallel fan-out branch node.
- retry_workflow_node:        Re-execute a failed node after its retry backoff.
- workflow_subagent_timeout:  Fail a run that waited too long for a subagent.
- workflow_subagent_complete: Resume a run whose spawned subagent finished.
"""

from __future__ import annotations

import uuid
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
    rather than advance past it (``resume_at_port`` advances FROM a finished
    node, so it would skip the branch's first node). ``from_edge`` is the
    spawning edge id (ASYNC-9).
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
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        await engine.retry_node(uuid.UUID(run_id), node_id)
        await db.commit()
        await engine.dispatch_enqueues(ctx["redis"])  # ASYNC-6: reuse worker pool

    # The retry counter (wf:retry:{run_id}:{node_id}) has a 1-hour TTL set by
    # the engine when it increments it.  Do NOT delete it here — if the node
    # failed again the engine already re-incremented and enqueued another retry;
    # deleting it would reset the counter to 0 and cause an infinite retry loop.

    logger.bind(event="workflow_node_retried", run_id=run_id, node_id=node_id).info("workflow node retried")
    return "ok"


async def workflow_subagent_timeout(
    ctx: dict[str, Any],
    run_id: str,
    node_id: str,
) -> str:
    """W10: Fail the run if a spawned subagent never completes within its timeout."""
    from shared_kernel.db.session import async_session

    async with async_session() as db:
        from contexts.workflow.application.run_engine import RunEngine

        engine = RunEngine(db)
        failed = await engine.force_fail(
            uuid.UUID(run_id), reason=f"subagent_timeout (node {node_id})"
        )
        if failed:
            logger.bind(run_id=run_id, node_id=node_id).warning("subagent timeout: failing run")
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
