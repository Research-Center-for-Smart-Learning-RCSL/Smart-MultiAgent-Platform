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
from contexts.workflow.application.step_recorder import StepRecorder
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
from contexts.workflow.infrastructure.channels import workflow_channel
from contexts.workflow.infrastructure.repositories import (
    WorkflowRunRepository,
    WorkflowStepRepository,
)
from shared_kernel import audit
from shared_kernel.observability.metrics import WORKFLOW_RUNS_TOTAL
from shared_kernel.realtime.pubsub import Publisher

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

# Each entry: (arq_task_name, run_id_str, node_id, delay_ms, from_edge_id).
# from_edge_id is set only for parallel-branch run_workflow_step tasks so a join
# placed directly after the parallel node can still dedupe arrivals (ASYNC-9);
# every other task type carries None.
_PendingEnqueue = tuple[str, str, str, int, str | None]


class RunEngine:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._runs = WorkflowRunRepository(db)
        self._steps = WorkflowStepRepository(db)
        self._recorder = StepRecorder(db, self._steps)
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
        # DB-1 transaction contract: the engine never commits or rolls back.
        # The caller (API endpoint or Arq task) owns the transaction, commits
        # exactly once, then calls dispatch_enqueues(). If _execute_node raises,
        # the caller rolls back — the run row inserted above is undone with it,
        # so there is no half-finished run left behind to repair here.
        try:
            await self._execute_node(ctx, entry_node_id)
        except Exception:
            logger.exception("run %s failed unexpectedly during start", run.id)
            raise

        return run.id

    async def resume_step(
        self,
        run_id: uuid.UUID,
        node_id: str,
    ) -> None:
        """Resume a parked run by advancing FROM an already-finished node.

        Used after an unpark (wait_for_event / approval): the node's work is
        done, so the engine follows its outgoing edges. To *execute* a node —
        e.g. a parallel fan-out branch — use :meth:`run_step` instead.
        """
        ctx = await self._prepare_continuation(run_id)
        if ctx is None:
            return

        # W11: on an unexpected error the caller rolls back this resume's
        # pending writes, leaving the run stuck RUNNING/WAITING. Persist FAILED
        # on an independent session so the marker survives that rollback, then
        # re-raise for the caller to roll back and log.
        try:
            await self._advance_from(ctx, node_id)
        except Exception:
            logger.exception(
                "run %s failed unexpectedly while resuming at node %s",
                run_id,
                node_id,
            )
            await self._mark_run_failed_isolated(run_id)
            raise

    async def run_step(
        self,
        run_id: uuid.UUID,
        node_id: str,
        from_edge: str | None = None,
    ) -> None:
        """Execute ``node_id`` as a parallel fan-out branch, then follow its edges.

        The parallel fan-out (W1) enqueues one ``run_workflow_step`` per outgoing
        edge; each must *run* its target node. This differs from
        :meth:`resume_step`, which advances FROM a node that already finished — a
        branch's first node has not run yet, so ``resume_step`` would skip it.
        ``from_edge`` is the spawning edge id, threaded so a join sitting
        immediately after the parallel node can dedupe arrivals per branch
        (ASYNC-9).
        """
        ctx = await self._prepare_continuation(run_id)
        if ctx is None:
            return

        try:
            await self._execute_node(ctx, node_id, from_edge=from_edge)
        except Exception:
            logger.exception(
                "run %s failed unexpectedly while running branch node %s",
                run_id,
                node_id,
            )
            await self._mark_run_failed_isolated(run_id)
            raise

    async def _prepare_continuation(
        self,
        run_id: uuid.UUID,
    ) -> RunContext | None:
        """Load a RUNNING/WAITING run and build its RunContext.

        Shared by :meth:`resume_step` and :meth:`run_step`. Returns ``None`` when
        the run is gone or no longer continuable; if the workflow definition was
        deleted the run is marked FAILED here (W8). DB-1: the caller owns commit.
        """
        run = await self._runs.get(run_id)
        if not run or run.state not in (RunState.RUNNING, RunState.WAITING):
            return None

        from contexts.workflow.infrastructure.repositories import WorkflowRepository

        workflow = await WorkflowRepository(self._db).get(run.workflow_id)
        if not workflow:
            # W8: log and fail the run instead of silently returning.
            logger.warning(
                "run %s cannot continue: workflow %s has been deleted",
                run_id,
                run.workflow_id,
            )
            await self._runs.update_state(run_id, state=RunState.FAILED, ended_at=datetime.now(UTC))
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="workflow.run_finished",
                    resource_type="workflow_run",
                    resource_id=run_id,
                    metadata={"final_state": "failed", "reason": "workflow deleted"},
                ),
            )
            return None

        return RunContext(
            run_id=run.id,
            workflow_id=run.workflow_id,
            workflow_def=workflow.definition,
            variables=dict(run.variables),
            trigger_payload=run.context.get("trigger_payload", {}),
        )

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
    ) -> bool:
        """Resume a WAITING run at an explicit output port (W6/W10 — event/timeout).

        Returns ``True`` only when the run was actually resumed (the parked step
        sealed and edges followed). Returns ``False`` when nothing was resumed:
        the run is gone, already terminal, or simply not WAITING — and also when
        the workflow definition was deleted (the run is failed here; the caller
        sees ``False``, re-checks the run state, finds it terminal, and drops
        its claim instead of retrying).

        One-wait-per-run limitation: run state is a single per-run FSM column,
        so the engine can only observe "this run is WAITING", not *which* parked
        node it is waiting on. With parallel branches, a second branch may be
        parked while the first one is being resumed (state RUNNING) — this
        method then returns ``False`` for the second branch's resume. Callers
        that claimed a single-shot resume token (``wf:wait:*`` / ``wf:approval:*``
        / ``wf:instruct:*``) MUST restore the claim and retry later on a
        ``False`` + non-terminal run, or the wait is lost (claim-before-verify).
        """
        from contexts.workflow.infrastructure.repositories import WorkflowRepository

        run = await self._runs.get(run_id)
        if not run or run.state != RunState.WAITING:
            return False

        workflow = await WorkflowRepository(self._db).get(run.workflow_id)
        if not workflow:
            logger.warning("resume_at_port: workflow %s deleted; failing run %s", run.workflow_id, run_id)
            now = datetime.now(UTC)
            await self._runs.update_state(run_id, state=RunState.FAILED, ended_at=now)
            return False

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
        from sqlalchemy.dialects import postgresql as pg

        from contexts.workflow.infrastructure.tables import workflow_steps

        # History accuracy (K remediation): resuming at a timeout/failure port
        # must not seal the parked step as `succeeded`. StepState has no
        # `timeout`, so those ports map to `failed`; the actual resume port is
        # stashed in the step output either way. A `rejected` approval gate is a
        # successfully *resolved* gate, so it stays `succeeded` with its port
        # recorded.
        sealed_state = "failed" if port in ("timeout", "failure") else "succeeded"
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
            .values(
                state=sealed_state,
                ended_at=sa.text("now()"),
                output=workflow_steps.c.output.op("||")(sa.cast({"resume_port": port}, pg.JSONB)),
            )
        )

        await self._advance_from(ctx, node_id, port=port)
        return True

    async def force_fail(self, run_id: uuid.UUID, *, reason: str) -> bool:
        """Fail a RUNNING/WAITING run from outside the execution loop (K.4 watchdog).

        Used by the timeout watchdog when ``run_max_seconds`` / ``idle_max_seconds``
        is exceeded. Idempotent: a run that already reached a terminal state is
        left untouched and ``False`` is returned. DB-1: the caller owns commit.
        """
        run = await self._runs.get(run_id)
        if not run or run.state not in (RunState.RUNNING, RunState.WAITING):
            return False

        now = datetime.now(UTC)
        await self._runs.update_state(run_id, state=RunState.FAILED, ended_at=now)
        await self._steps.cancel_pending_for_run(run_id)
        WORKFLOW_RUNS_TOTAL.labels(state="failed").inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="workflow.run_finished",
                resource_type="workflow_run",
                resource_id=run_id,
                metadata={"final_state": "failed", "reason": reason},
            ),
        )
        await Publisher(workflow_channel(run_id)).emit(
            "workflow.run_finished",
            {"run_id": str(run_id), "state": "failed", "reason": reason},
        )
        return True

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

    async def _mark_run_failed_isolated(self, run_id: uuid.UUID) -> None:
        """Persist a run's FAILED state on a fresh, independent session (W11).

        Crash-recovery helper: when the caller's transaction is being torn down
        by a failure, the FAILED marker must be written on a separate session
        so it is not lost in the caller's rollback. Best-effort — a failure to
        record it is logged, never raised.
        """
        from shared_kernel.db.session import async_session

        try:
            async with async_session() as session, session.begin():
                await WorkflowRunRepository(session).update_state(
                    run_id,
                    state=RunState.FAILED,
                    ended_at=datetime.now(UTC),
                )
        except Exception:
            logger.exception("could not persist FAILED state for run %s", run_id)

    async def dispatch_enqueues(self, pool: Any | None = None) -> None:
        """Enqueue pending Arq tasks (W1/W3/W6/W10).

        DB-1 contract: the caller MUST have committed the transaction first, so
        a worker that picks up an enqueued job can see the run row. Entry points
        call this immediately after their single commit.

        ASYNC-6: a worker task hands in its own long-lived Arq pool
        (``ctx["redis"]``) via ``pool`` so nothing is opened here. On the API
        path — which has no Arq ctx — ``pool`` is ``None``; a short-lived pool
        is created and *always* closed in the ``finally`` block (including its
        underlying connection pool), so Redis connections are never leaked.
        """
        if not self._pending_enqueues:
            return
        pending = list(self._pending_enqueues)
        self._pending_enqueues.clear()

        owns_pool = pool is None
        if pool is None:
            from arq.connections import RedisSettings, create_pool

            from app.config.settings import get_settings

            pool = await create_pool(RedisSettings.from_dsn(get_settings().redis.dsn))
        try:
            for task_name, run_id_str, node_id, delay_ms, from_edge in pending:
                kwargs: dict[str, Any] = {}
                if delay_ms > 0:
                    # arq's deferral parameter is ``_defer_by`` (leading
                    # underscore). The bare ``defer_by`` used previously fell
                    # through to ``**kwargs`` and was passed as a *job argument*
                    # — the timeout/retry task then crashed on an unexpected
                    # keyword and the delay was never applied (K.4).
                    kwargs["_defer_by"] = timedelta(milliseconds=delay_ms)
                # run_workflow_step carries the spawning edge id (ASYNC-9); the
                # other task types take only (run_id, node_id).
                if from_edge is not None:
                    await pool.enqueue_job(task_name, run_id_str, node_id, from_edge, **kwargs)
                else:
                    await pool.enqueue_job(task_name, run_id_str, node_id, **kwargs)
        finally:
            if owns_pool:
                # close_connection_pool=True is required: create_pool builds the
                # ArqRedis with an externally-supplied connection pool, so a bare
                # aclose() would not disconnect the pooled sockets.
                await pool.aclose(close_connection_pool=True)

    async def _execute_node(
        self,
        ctx: RunContext,
        node_id: str,
        *,
        from_edge: str | None = None,
    ) -> None:
        """Execute one node and follow edges.

        ``from_edge`` is the id of the edge traversed to reach this node; the
        join executor reads it (via ``ctx.arrived_via``) to dedupe fan-in
        arrivals per incoming branch, so an Arq re-delivery / retry of a branch
        cannot inflate the arrival count (ASYNC-9).
        """
        if ctx.cancelled:
            return

        # ASYNC-9: surface the traversed edge to the executor layer.
        ctx.arrived_via = from_edge

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

        # Insert step record and emit started event
        step = await self._recorder.insert_step(
            run_id=ctx.run_id,
            node_id=node_id,
            input_data=node.config,
        )
        await self._recorder.emit_step_started(
            run_id=ctx.run_id,
            step_id=step.id,
            node_id=node_id,
            node_type=node.type.value,
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

        # Update step record, observe metrics, flush, and emit event
        await self._recorder.update_step(
            step.id,
            outcome=outcome,
            node_type=node.type.value,
            step_start=step_start,
        )
        await self._recorder.emit_step_event(
            run_id=ctx.run_id,
            step_id=step.id,
            node_id=node_id,
            outcome=outcome,
        )

        # Update run variables
        await self._runs.update_variables(ctx.run_id, ctx.variables)

        # K.4: a set_variable change can satisfy a sibling branch parked on a
        # variable_matches wait. Signal it *after* commit (this enqueue is
        # dispatched by dispatch_enqueues post-commit) so the resume reads the
        # committed variables. Reuses the (run_id, node_id) enqueue shape.
        if node.type == NodeType.SET_VARIABLE and outcome.state == StepState.SUCCEEDED:
            self._pending_enqueues.append(("workflow_variable_signal", str(ctx.run_id), node_id, 0, None))

        # Parked — set run to WAITING, schedule any timeout task, then stop.
        #
        # One-wait-per-run-at-a-time: WAITING is a single per-run FSM column.
        # If parallel branches park more than one node, the run still has only
        # one observable WAITING state, and resume_at_port() resumes whichever
        # claim fires first — a second resume arriving while the run is RUNNING
        # returns False and must be retried by its (restored) claim holder.
        # Note also that some parked executors (wait_for_event) write their
        # Redis claim key *inside* this transaction's executor call, i.e. the
        # key may be visible to dispatchers before this WAITING update commits;
        # resume tasks therefore tolerate a not-yet-WAITING run by retrying.
        if outcome.park:
            await self._runs.update_state(ctx.run_id, state=RunState.WAITING)
            if outcome.timeout_ms > 0 and outcome.timeout_task:
                self._pending_enqueues.append(
                    (outcome.timeout_task, str(ctx.run_id), node_id, outcome.timeout_ms, None),
                )
            return

        # W2: step succeeded but edges should not be followed (e.g. non-final join arrival).
        if outcome.skip_edges:
            return

        # End node — finish the run
        if node.type == NodeType.END:
            end_status = node.config.get("status", "success")
            final_state = RunState.SUCCEEDED if end_status == "success" else RunState.FAILED
            end_now = datetime.now(UTC)
            await self._runs.update_state(
                ctx.run_id,
                state=final_state,
                ended_at=end_now,
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
            await Publisher(workflow_channel(ctx.run_id)).emit(
                action,
                {
                    "run_id": str(ctx.run_id),
                    "state": final_state.value,
                },
            )
            return

        # Failed with no recovery — fail the run
        if outcome.state == StepState.FAILED:
            await self._fail_run(ctx, outcome.error or "Step failed")
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
            await self._execute_node(ctx, matching[0].to_node, from_edge=matching[0].id)
        else:
            # W1: multiple outgoing edges → parallel branches.
            # Enqueue each as an independent Arq task so branches truly run in parallel
            # with their own DB sessions; avoids sequential execution and session contention.
            ctx.active_branches = len(matching)
            for edge in matching:
                # ASYNC-9: carry the spawning edge id so a join immediately
                # downstream of the parallel node dedupes arrivals per branch.
                self._pending_enqueues.append(
                    ("run_workflow_step", str(ctx.run_id), edge.to_node, 0, edge.id),
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
            # get_redis() uses decode_responses=True, so ``raw`` is already a
            # str — calling .decode() on it raised AttributeError and failed
            # the whole run on the first retryable error (K remediation).
            raw = await redis.get(redis_key)
            retry_count = int(raw) if raw else 0

            if retry_count < node.on_error.retry_max:
                new_count = retry_count + 1
                backoff_ms = min(node.on_error.retry_backoff_ms * new_count, 60_000)
                await redis.set(redis_key, str(new_count), ex=3600)
                self._pending_enqueues.append(
                    ("retry_workflow_node", str(ctx.run_id), node.id, backoff_ms, None),
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
