"""Workflow run engine — DAG+FSM executor (H.4).

Drives a workflow run through its nodes. Each invocation processes one
node (or resumes from a parked state), persists a workflow_steps row,
and either re-enqueues (next node) or parks (wait_for_event / approval).

Key responsibilities:
- Load run state, advance to next ready node.
- Execute the node via its registered executor.
- Apply on-error strategy (fail/continue/retry/fallback).
- Record workflow_steps.
- Honour run_max_seconds, idle_max_seconds, loop_guard.
- Parallel fan-out: enqueue one Arq task per outgoing edge (W1).
- Publish step events to WebSocket channel.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors import get_executor
from contexts.workflow.domain.models import (
    EdgeSpec,
    NodeSpec,
    NodeType,
    OnErrorConfig,
    OnErrorStrategy,
    RunContext,
    RunState,
    StepOutcome,
    StepState,
)
from contexts.workflow.infrastructure.repositories import (
    WorkflowRunRepository,
    WorkflowStepRepository,
)
from shared_kernel import audit
from shared_kernel.observability.metrics import (
    WORKFLOW_RUNS_TOTAL,
    WORKFLOW_STEP_DURATION_SECONDS,
    WORKFLOW_STEPS_TOTAL,
)
from shared_kernel.realtime.pubsub import Publisher, workflow_channel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Definition parsing helpers
# ---------------------------------------------------------------------------


def _parse_node(raw: dict[str, Any]) -> NodeSpec:
    config = dict(raw.get("config", {}))
    on_error_raw = config.get("on_error") or {}
    clean_config = {k: v for k, v in config.items() if k != "on_error"}
    return NodeSpec(
        id=raw["id"],
        type=NodeType(raw["type"]),
        config=clean_config,
        label=raw.get("label"),
        on_error=OnErrorConfig(
            strategy=OnErrorStrategy(on_error_raw.get("strategy", "fail")),
            retry_max=on_error_raw.get("retry_max", 0),
            retry_backoff_ms=on_error_raw.get("retry_backoff_ms", 500),
            fallback_node_id=on_error_raw.get("fallback_node_id"),
        ),
    )


def _parse_edges(raw_edges: list[dict[str, Any]]) -> list[EdgeSpec]:
    return [
        EdgeSpec(
            id=e["id"],
            from_node=e["from"],
            to_node=e["to"],
            from_port=e.get("from_port", "default"),
            guard=e.get("guard"),
        )
        for e in raw_edges
    ]


def _build_outgoing(edges: list[EdgeSpec]) -> dict[str, list[EdgeSpec]]:
    out: dict[str, list[EdgeSpec]] = defaultdict(list)
    for e in edges:
        out[e.from_node].append(e)
    return dict(out)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

# Each entry: (arq_task_name, run_id_str, node_id, delay_ms)
_PendingEnqueue = tuple[str, str, str, int]


class RunEngine:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._runs = WorkflowRunRepository(db)
        self._steps = WorkflowStepRepository(db)
        # W1/W3/W6/W10: collected during execution, dispatched after commit.
        self._pending_enqueues: list[_PendingEnqueue] = []

    # -- public API --

    async def start_run(
        self,
        *,
        project_id: uuid.UUID,
        workflow_id: uuid.UUID,
        definition: dict[str, Any],
        trigger_type: str,
        started_by_user_id: uuid.UUID | None = None,
        trigger_payload: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """Create a new workflow run and execute the entry node."""
        variables = {}
        for var_name, var_def in definition.get("variables", {}).items():
            variables[var_name] = var_def.get("default")

        run = await self._runs.insert(
            project_id=project_id,
            workflow_id=workflow_id,
            trigger_type=trigger_type,
            started_by_user_id=started_by_user_id,
            variables=variables,
            context={"trigger_payload": trigger_payload or {}},
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.run_started",
                resource_type="workflow_run",
                resource_id=run.id,
                actor_user_id=started_by_user_id,
                metadata={"workflow_id": str(workflow_id), "trigger_type": trigger_type},
            ),
        )

        pub = Publisher(workflow_channel(run.id))
        await pub.emit(
            "workflow.run_started",
            {
                "run_id": str(run.id),
                "workflow_id": str(workflow_id),
            },
        )

        ctx = RunContext(
            run_id=run.id,
            workflow_id=workflow_id,
            workflow_def=definition,
            variables=variables,
            trigger_payload=trigger_payload or {},
        )

        entry_node_id = definition.get("entry_node_id", "")
        # W11: catch unexpected exceptions so we can mark the run FAILED instead of
        # leaving it in a limbo state.
        try:
            await self._execute_node(ctx, entry_node_id)
            await self._db.commit()
        except Exception as exc:
            logger.exception("run %s failed unexpectedly during start: %s", run.id, exc)
            try:
                await self._db.rollback()
                if not ctx.cancelled:
                    now = datetime.now(UTC)
                    await self._runs.update_state(run.id, state=RunState.FAILED, ended_at=now)
                    await self._db.commit()
            except Exception:
                logger.exception("could not persist FAILED state for run %s", run.id)
            raise

        await self._dispatch_enqueues()
        return run.id

    async def resume_step(
        self,
        run_id: uuid.UUID,
        node_id: str,
    ) -> None:
        """Resume a parked run at a specific node."""
        run = await self._runs.get(run_id)
        if not run or run.state not in (RunState.RUNNING, RunState.WAITING):
            return

        from contexts.workflow.infrastructure.repositories import WorkflowRepository

        wf_repo = WorkflowRepository(self._db)
        workflow = await wf_repo.get(run.workflow_id)
        if not workflow:
            # W8: log and fail the run instead of silently returning.
            logger.warning(
                "run %s cannot resume: workflow %s has been deleted",
                run_id,
                run.workflow_id,
            )
            now = datetime.now(UTC)
            await self._runs.update_state(run_id, state=RunState.FAILED, ended_at=now)
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="workflow.run_finished",
                    resource_type="workflow_run",
                    resource_id=run_id,
                    metadata={"final_state": "failed", "reason": "workflow deleted"},
                ),
            )
            await self._db.commit()
            return

        ctx = RunContext(
            run_id=run.id,
            workflow_id=run.workflow_id,
            workflow_def=workflow.definition,
            variables=dict(run.variables),
            trigger_payload=run.context.get("trigger_payload", {}),
        )

        # W11: catch unexpected exceptions to prevent the run from being stuck WAITING.
        try:
            await self._advance_from(ctx, node_id)
            await self._db.commit()
        except Exception as exc:
            logger.exception(
                "run %s failed unexpectedly while resuming at node %s: %s",
                run_id,
                node_id,
                exc,
            )
            try:
                await self._db.rollback()
                if not ctx.cancelled:
                    now = datetime.now(UTC)
                    await self._runs.update_state(run_id, state=RunState.FAILED, ended_at=now)
                    await self._db.commit()
            except Exception:
                logger.exception("could not persist FAILED state for run %s", run_id)
            raise

        await self._dispatch_enqueues()

    async def retry_node(self, run_id: uuid.UUID, node_id: str) -> None:
        """Re-execute a failed node as part of the retry strategy (W3)."""
        import sqlalchemy as sa

        from contexts.workflow.infrastructure.repositories import WorkflowRepository
        from contexts.workflow.infrastructure.tables import workflow_steps

        run = await self._runs.get(run_id)
        if not run or run.state not in (RunState.RUNNING, RunState.WAITING):
            return

        workflow = await WorkflowRepository(self._db).get(run.workflow_id)
        if not workflow:
            return

        # Cancel the "awaiting retry" placeholder step.
        await self._db.execute(
            workflow_steps.update()
            .where(
                sa.and_(
                    workflow_steps.c.run_id == run_id,
                    workflow_steps.c.node_id == node_id,
                    workflow_steps.c.state == "running",
                    workflow_steps.c.ended_at.is_(None),
                )
            )
            .values(state="cancelled", ended_at=sa.text("now()"))
        )

        # Restore run to RUNNING (it was parked WAITING during the backoff delay).
        await self._runs.update_state(run_id, state=RunState.RUNNING)

        ctx = RunContext(
            run_id=run_id,
            workflow_id=run.workflow_id,
            workflow_def=workflow.definition,
            variables=dict(run.variables),
            trigger_payload=run.context.get("trigger_payload", {}),
        )

        await self._execute_node(ctx, node_id)

    async def resume_at_port(
        self,
        run_id: uuid.UUID,
        node_id: str,
        port: str,
    ) -> None:
        """Resume a WAITING run at an explicit output port (W6/W10 — event/timeout)."""
        from contexts.workflow.infrastructure.repositories import WorkflowRepository

        run = await self._runs.get(run_id)
        if not run or run.state != RunState.WAITING:
            return

        workflow = await WorkflowRepository(self._db).get(run.workflow_id)
        if not workflow:
            logger.warning("resume_at_port: workflow %s deleted; failing run %s", run.workflow_id, run_id)
            now = datetime.now(UTC)
            await self._runs.update_state(run_id, state=RunState.FAILED, ended_at=now)
            return

        ctx = RunContext(
            run_id=run_id,
            workflow_id=run.workflow_id,
            workflow_def=workflow.definition,
            variables=dict(run.variables),
            trigger_payload=run.context.get("trigger_payload", {}),
        )

        await self._runs.update_state(run_id, state=RunState.RUNNING)

        # Close the parked step so it doesn't remain RUNNING indefinitely.
        # Parked executors (wait_for_event, subagent_spawn) leave their step in
        # RUNNING/no-ended_at; the resume path must seal it before advancing.
        import sqlalchemy as sa

        from contexts.workflow.infrastructure.tables import workflow_steps

        await self._db.execute(
            workflow_steps.update()
            .where(
                sa.and_(
                    workflow_steps.c.run_id == run_id,
                    workflow_steps.c.node_id == node_id,
                    workflow_steps.c.state == "running",
                    workflow_steps.c.ended_at.is_(None),
                )
            )
            .values(state="succeeded", ended_at=sa.text("now()"))
        )

        await self._advance_from(ctx, node_id, port=port)

    async def cancel_run(self, run_id: uuid.UUID) -> None:
        """Cancel a running/waiting workflow run."""
        run = await self._runs.get(run_id)
        if not run:
            return
        if run.state not in (RunState.RUNNING, RunState.WAITING):
            return

        now = datetime.now(UTC)
        await self._runs.update_state(
            run_id,
            state=RunState.CANCELLED,
            ended_at=now,
        )
        await self._steps.cancel_pending_for_run(run_id)
        WORKFLOW_RUNS_TOTAL.labels(state="cancelled").inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.run_cancelled",
                resource_type="workflow_run",
                resource_id=run_id,
            ),
        )

        pub = Publisher(workflow_channel(run_id))
        await pub.emit("workflow.run_cancelled", {"run_id": str(run_id)})

    # -- internal --

    async def _dispatch_enqueues(self) -> None:
        """Enqueue pending Arq tasks after the DB transaction is committed (W1/W3/W6/W10)."""
        if not self._pending_enqueues:
            return
        pending = list(self._pending_enqueues)
        self._pending_enqueues.clear()

        from arq.connections import RedisSettings, create_pool

        from app.config.settings import get_settings

        pool = await create_pool(RedisSettings.from_dsn(get_settings().redis.dsn))
        for task_name, run_id_str, node_id, delay_ms in pending:
            kwargs: dict[str, Any] = {}
            if delay_ms > 0:
                kwargs["defer_by"] = timedelta(milliseconds=delay_ms)
            await pool.enqueue_job(task_name, run_id_str, node_id, **kwargs)

    async def _execute_node(self, ctx: RunContext, node_id: str) -> None:
        """Execute one node and follow edges."""
        if ctx.cancelled:
            return

        # Loop guard
        ctx.node_visit_counts[node_id] = ctx.node_visit_counts.get(node_id, 0) + 1
        if ctx.node_visit_counts[node_id] > ctx.max_visits_per_node:
            await self._fail_run(
                ctx,
                f"Loop guard: node '{node_id}' visited {ctx.node_visit_counts[node_id]} times",
            )
            return

        # Find node
        nodes_by_id = {n["id"]: n for n in ctx.workflow_def.get("nodes", [])}
        raw_node = nodes_by_id.get(node_id)
        if not raw_node:
            await self._fail_run(ctx, f"Node '{node_id}' not found in definition")
            return

        node = _parse_node(dict(raw_node))  # copy to avoid mutating def
        executor = get_executor(node.type)

        # Insert step record
        step = await self._steps.insert(
            run_id=ctx.run_id,
            node_id=node_id,
            state=StepState.RUNNING,
            input_data=node.config,
        )

        pub = Publisher(workflow_channel(ctx.run_id))
        await pub.emit(
            "workflow.step_started",
            {
                "step_id": str(step.id),
                "node_id": node_id,
                "node_type": node.type.value,
            },
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.step_started",
                resource_type="workflow_step",
                resource_id=step.id,
                metadata={"run_id": str(ctx.run_id), "node_id": node_id},
            ),
        )

        # Execute
        step_start = time.monotonic()
        try:
            outcome = await executor(ctx, node, self._db)
        except Exception as exc:
            outcome = StepOutcome(
                state=StepState.FAILED,
                error=str(exc),
            )

        # Apply on-error strategy if failed (W21: log the failure first)
        if outcome.state == StepState.FAILED:
            logger.warning(
                "run %s: node %s failed (strategy=%s): %s",
                ctx.run_id,
                node_id,
                node.on_error.strategy.value,
                outcome.error,
            )
            outcome = await self._apply_on_error(ctx, node, outcome, step.id)

        # Update step record
        now = datetime.now(UTC)
        await self._steps.update(
            step.id,
            state=outcome.state,
            ended_at=now if outcome.state not in (StepState.RUNNING, StepState.PENDING) else None,
            output=outcome.output,
            error=outcome.error,
        )
        _step_elapsed = time.monotonic() - step_start
        WORKFLOW_STEP_DURATION_SECONDS.labels(node_type=node.type.value).observe(_step_elapsed)
        if outcome.state not in (StepState.RUNNING, StepState.PENDING):
            WORKFLOW_STEPS_TOTAL.labels(
                node_type=node.type.value,
                state=outcome.state.value,
            ).inc()

        # W12: flush step outcome durably before following edges so that if
        # _advance_from raises, the step result is preserved in the eventual commit.
        await self._db.flush()

        event_action = (
            "workflow.step_failed" if outcome.state == StepState.FAILED else "workflow.step_finished"
        )
        await pub.emit(
            event_action,
            {
                "step_id": str(step.id),
                "node_id": node_id,
                "state": outcome.state.value,
            },
        )

        # Update run variables
        await self._runs.update_variables(ctx.run_id, ctx.variables)

        # Parked — set run to WAITING, schedule any timeout task, then stop.
        if outcome.park:
            await self._runs.update_state(ctx.run_id, state=RunState.WAITING)
            if outcome.timeout_ms > 0 and outcome.timeout_task:
                self._pending_enqueues.append(
                    (outcome.timeout_task, str(ctx.run_id), node_id, outcome.timeout_ms),
                )
            return

        # W2: step succeeded but edges should not be followed (e.g. non-final join arrival).
        if outcome.skip_edges:
            return

        # Failed with no recovery — fail the run
        if outcome.state == StepState.FAILED:
            await self._fail_run(ctx, outcome.error or "Step failed")
            return

        # End node — finish the run
        if node.type == NodeType.END:
            end_status = node.config.get("status", "success")
            final_state = RunState.SUCCEEDED if end_status == "success" else RunState.FAILED
            await self._runs.update_state(
                ctx.run_id,
                state=final_state,
                ended_at=now,
            )
            WORKFLOW_RUNS_TOTAL.labels(state=final_state.value).inc()
            action = "workflow.run_finished"
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action=action,
                    resource_type="workflow_run",
                    resource_id=ctx.run_id,
                    metadata={"final_state": final_state.value},
                ),
            )
            await pub.emit(
                action,
                {
                    "run_id": str(ctx.run_id),
                    "state": final_state.value,
                },
            )
            return

        # Follow outgoing edges
        await self._advance_from(ctx, node_id, port=outcome.port)

    async def _advance_from(
        self,
        ctx: RunContext,
        node_id: str,
        *,
        port: str = "default",
    ) -> None:
        """Follow edges from a node's output port."""
        edges = _parse_edges(ctx.workflow_def.get("edges", []))
        outgoing = _build_outgoing(edges)

        matching = [e for e in outgoing.get(node_id, []) if e.from_port == port]

        # For parallel nodes, take ALL outgoing default edges
        nodes_by_id = {n["id"]: n for n in ctx.workflow_def.get("nodes", [])}
        raw_node = nodes_by_id.get(node_id, {})
        if raw_node.get("type") == "parallel":
            matching = outgoing.get(node_id, [])

        if not matching:
            return

        if len(matching) == 1:
            await self._execute_node(ctx, matching[0].to_node)
        else:
            # W1: multiple outgoing edges → parallel branches.
            # Enqueue each as an independent Arq task so branches truly run in parallel
            # with their own DB sessions; avoids sequential execution and session contention.
            ctx.active_branches = len(matching)
            for edge in matching:
                self._pending_enqueues.append(
                    ("run_workflow_step", str(ctx.run_id), edge.to_node, 0),
                )

    async def _apply_on_error(
        self,
        ctx: RunContext,
        node: NodeSpec,
        outcome: StepOutcome,
        step_id: uuid.UUID,
    ) -> StepOutcome:
        """Apply the node's on_error strategy to a failed outcome."""
        strategy = node.on_error.strategy

        if strategy == OnErrorStrategy.CONTINUE:
            return StepOutcome(
                state=StepState.SUCCEEDED,
                output=outcome.output,
                port="default",
            )

        if strategy == OnErrorStrategy.RETRY:
            # W3: use a Redis counter + Arq delayed re-enqueue instead of asyncio.sleep,
            # which held the DB connection and Arq job slot for up to 60 s per retry.
            from shared_kernel.auth.clients import get_redis

            redis = get_redis()
            redis_key = f"wf:retry:{ctx.run_id}:{node.id}"
            raw = await redis.get(redis_key)
            retry_count = int(raw.decode()) if raw else 0

            if retry_count < node.on_error.retry_max:
                new_count = retry_count + 1
                backoff_ms = min(node.on_error.retry_backoff_ms * new_count, 60_000)
                await redis.set(redis_key, str(new_count), ex=3600)
                self._pending_enqueues.append(
                    ("retry_workflow_node", str(ctx.run_id), node.id, backoff_ms),
                )
                logger.info(
                    "run %s: node %s retry %d/%d scheduled in %d ms",
                    ctx.run_id,
                    node.id,
                    new_count,
                    node.on_error.retry_max,
                    backoff_ms,
                )
                return StepOutcome(
                    state=StepState.RUNNING,
                    output={"__retry_count": new_count},
                    park=True,
                )
            logger.warning(
                "run %s: node %s exhausted all %d retries; failing",
                ctx.run_id,
                node.id,
                node.on_error.retry_max,
            )

        if strategy == OnErrorStrategy.FALLBACK:
            # W4: execute fallback node then use skip_edges so we don't double-advance.
            # The fallback's own _execute_node call already follows its outgoing edges.
            fallback_id = node.on_error.fallback_node_id
            if fallback_id:
                logger.info(
                    "run %s: applying fallback node %s for failed node %s",
                    ctx.run_id,
                    fallback_id,
                    node.id,
                )
                await self._execute_node(ctx, fallback_id)
                return StepOutcome(
                    state=StepState.SUCCEEDED,
                    output={"fallback_node": fallback_id},
                    skip_edges=True,
                )
            logger.warning(
                "run %s: node %s has fallback strategy but no fallback_node_id configured; failing",
                ctx.run_id,
                node.id,
            )

        # Default: FAIL — return the original failed outcome
        return outcome

    async def _fail_run(self, ctx: RunContext, reason: str) -> None:
        """Mark a run as failed, cancel pending steps."""
        ctx.cancelled = True
        now = datetime.now(UTC)
        await self._runs.update_state(
            ctx.run_id,
            state=RunState.FAILED,
            ended_at=now,
        )
        await self._steps.cancel_pending_for_run(ctx.run_id)
        WORKFLOW_RUNS_TOTAL.labels(state="failed").inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.run_finished",
                resource_type="workflow_run",
                resource_id=ctx.run_id,
                metadata={"final_state": "failed", "reason": reason},
            ),
        )

        pub = Publisher(workflow_channel(ctx.run_id))
        await pub.emit(
            "workflow.run_finished",
            {
                "run_id": str(ctx.run_id),
                "state": "failed",
                "reason": reason,
            },
        )
