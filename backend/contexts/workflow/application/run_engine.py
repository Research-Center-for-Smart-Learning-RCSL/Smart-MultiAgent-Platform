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
- Parallel fan-out: enqueue one task per outgoing edge.
- Publish step events to WebSocket channel.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors import get_executor
from contexts.workflow.domain.errors import (
    LoopGuardExceeded,
    WorkflowRunCancelled,
    WorkflowRunTimeout,
)
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


class RunEngine:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._runs = WorkflowRunRepository(db)
        self._steps = WorkflowStepRepository(db)

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

        await audit.emit(self._db, audit.AuditEvent(
            action="workflow.run_started",
            resource_type="workflow_run",
            resource_id=run.id,
            actor_user_id=started_by_user_id,
            metadata={"workflow_id": str(workflow_id), "trigger_type": trigger_type},
        ))

        pub = Publisher(workflow_channel(run.id))
        await pub.emit("workflow.run_started", {
            "run_id": str(run.id),
            "workflow_id": str(workflow_id),
        })

        ctx = RunContext(
            run_id=run.id,
            workflow_id=workflow_id,
            workflow_def=definition,
            variables=variables,
            trigger_payload=trigger_payload or {},
        )

        entry_node_id = definition.get("entry_node_id", "")
        await self._execute_node(ctx, entry_node_id)
        await self._db.commit()

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
            return

        ctx = RunContext(
            run_id=run.id,
            workflow_id=run.workflow_id,
            workflow_def=workflow.definition,
            variables=dict(run.variables),
            trigger_payload=run.context.get("trigger_payload", {}),
        )

        await self._advance_from(ctx, node_id)
        await self._db.commit()

    async def cancel_run(self, run_id: uuid.UUID) -> None:
        """Cancel a running/waiting workflow run."""
        run = await self._runs.get(run_id)
        if not run:
            return
        if run.state not in (RunState.RUNNING, RunState.WAITING):
            return

        now = datetime.now(timezone.utc)
        await self._runs.update_state(
            run_id, state=RunState.CANCELLED, ended_at=now,
        )
        await self._steps.cancel_pending_for_run(run_id)

        await audit.emit(self._db, audit.AuditEvent(
            action="workflow.run_cancelled",
            resource_type="workflow_run",
            resource_id=run_id,
        ))

        pub = Publisher(workflow_channel(run_id))
        await pub.emit("workflow.run_cancelled", {"run_id": str(run_id)})

    # -- internal --

    async def _execute_node(self, ctx: RunContext, node_id: str) -> None:
        """Execute one node and follow edges."""
        if ctx.cancelled:
            return

        # Loop guard
        ctx.node_visit_counts[node_id] = ctx.node_visit_counts.get(node_id, 0) + 1
        if ctx.node_visit_counts[node_id] > ctx.max_visits_per_node:
            await self._fail_run(ctx, f"Loop guard: node '{node_id}' visited {ctx.node_visit_counts[node_id]} times")
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
        await pub.emit("workflow.step_started", {
            "step_id": str(step.id),
            "node_id": node_id,
            "node_type": node.type.value,
        })

        await audit.emit(self._db, audit.AuditEvent(
            action="workflow.step_started",
            resource_type="workflow_step",
            resource_id=step.id,
            metadata={"run_id": str(ctx.run_id), "node_id": node_id},
        ))

        # Execute
        try:
            outcome = await executor(ctx, node, self._db)
        except Exception as exc:
            outcome = StepOutcome(
                state=StepState.FAILED,
                error=str(exc),
            )

        # Apply on-error strategy if failed
        if outcome.state == StepState.FAILED:
            outcome = await self._apply_on_error(ctx, node, outcome, step.id)

        # Update step
        now = datetime.now(timezone.utc)
        await self._steps.update(
            step.id,
            state=outcome.state,
            ended_at=now if outcome.state not in (StepState.RUNNING, StepState.PENDING) else None,
            output=outcome.output,
            error=outcome.error,
        )

        event_action = (
            "workflow.step_failed" if outcome.state == StepState.FAILED
            else "workflow.step_finished"
        )
        await pub.emit(event_action, {
            "step_id": str(step.id),
            "node_id": node_id,
            "state": outcome.state.value,
        })

        # Update run variables
        await self._runs.update_variables(ctx.run_id, ctx.variables)

        # Parked — set run to waiting, return without following edges
        if outcome.park:
            await self._runs.update_state(ctx.run_id, state=RunState.WAITING)
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
                ctx.run_id, state=final_state, ended_at=now,
            )
            action = "workflow.run_finished"
            await audit.emit(self._db, audit.AuditEvent(
                action=action,
                resource_type="workflow_run",
                resource_id=ctx.run_id,
                metadata={"final_state": final_state.value},
            ))
            await pub.emit(action, {
                "run_id": str(ctx.run_id),
                "state": final_state.value,
            })
            return

        # Follow outgoing edges
        await self._advance_from(ctx, node_id, port=outcome.port)

    async def _advance_from(
        self, ctx: RunContext, node_id: str, *, port: str = "default",
    ) -> None:
        """Follow edges from a node's output port."""
        edges = _parse_edges(ctx.workflow_def.get("edges", []))
        outgoing = _build_outgoing(edges)

        matching = [
            e for e in outgoing.get(node_id, [])
            if e.from_port == port
        ]

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
            # Multiple outgoing edges → parallel execution
            for edge in matching:
                await self._execute_node(ctx, edge.to_node)

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
            # Retry tracking via step output
            retry_count = outcome.output.get("__retry_count", 0)
            if retry_count < node.on_error.retry_max:
                import asyncio
                backoff_s = node.on_error.retry_backoff_ms / 1000.0 * (retry_count + 1)
                await asyncio.sleep(min(backoff_s, 60.0))
                executor = get_executor(node.type)
                try:
                    new_outcome = await executor(ctx, node, self._db)
                    if new_outcome.state != StepState.FAILED:
                        return new_outcome
                    new_outcome_output = {**new_outcome.output, "__retry_count": retry_count + 1}
                    return StepOutcome(
                        state=new_outcome.state,
                        output=new_outcome_output,
                        port=new_outcome.port,
                        error=new_outcome.error,
                    )
                except Exception as exc:
                    return StepOutcome(
                        state=StepState.FAILED,
                        output={"__retry_count": retry_count + 1},
                        error=str(exc),
                    )

        if strategy == OnErrorStrategy.FALLBACK:
            fallback_id = node.on_error.fallback_node_id
            if fallback_id:
                await self._execute_node(ctx, fallback_id)
                return StepOutcome(
                    state=StepState.SUCCEEDED,
                    output={"fallback": fallback_id},
                    port="default",
                    park=True,  # We already followed the fallback path
                )

        # Default: FAIL — return the original failed outcome
        return outcome

    async def _fail_run(self, ctx: RunContext, reason: str) -> None:
        """Mark a run as failed, cancel pending steps."""
        ctx.cancelled = True
        now = datetime.now(timezone.utc)
        await self._runs.update_state(
            ctx.run_id, state=RunState.FAILED, ended_at=now,
        )
        await self._steps.cancel_pending_for_run(ctx.run_id)

        await audit.emit(self._db, audit.AuditEvent(
            action="workflow.run_finished",
            resource_type="workflow_run",
            resource_id=ctx.run_id,
            metadata={"final_state": "failed", "reason": reason},
        ))

        pub = Publisher(workflow_channel(ctx.run_id))
        await pub.emit("workflow.run_finished", {
            "run_id": str(ctx.run_id),
            "state": "failed",
            "reason": reason,
        })
