"""agent_invocation executor — run one Agent turn, capture reply."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.sel.template import interpolate
from shared_kernel import audit


@register(NodeType.AGENT_INVOCATION)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    agent_id = config.get("agent_id", "")
    input_template = config.get("input_template", "")
    output_variable = config.get("output_variable")

    variables = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": _build_ctx_vars(ctx),
    }
    rendered_input = interpolate(input_template, variables)

    try:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)

        # F-24: if this run was started by an inbound A2A CALL signal, its chain
        # rode in on the trigger payload; thread it back into the call so the
        # cycle/depth guard sees the real chain (this worker is a different
        # process from the A2A consumer, so the ContextVar is empty here).
        trigger = ctx.trigger_payload or {}
        result = await facade.a2a_call(
            from_agent_id=None,
            to_agent_id=uuid.UUID(agent_id),
            payload={"input": rendered_input, "origin": "workflow"},
            workflow_run_id=ctx.run_id,
            timeout_seconds=float(config.get("timeout_seconds", 300)),
            inbound_call_depth=trigger.get("call_depth"),
            inbound_call_path=trigger.get("call_path"),
        )

        reply = result.get("reply", result.get("output", ""))
        if output_variable:
            ctx.variables[output_variable] = reply

        await audit.emit(
            db,
            audit.AuditEvent(
                action="workflow.step_finished",
                resource_type="workflow_step",
                resource_id=ctx.run_id,
                metadata={"node_id": node.id, "agent_id": agent_id, "origin": "workflow"},
            ),
        )

        return StepOutcome(
            state=StepState.SUCCEEDED,
            output={"reply": reply},
            port="success",
        )

    except Exception as exc:
        from contexts.orchestration.domain.errors import A2ACallCancelled

        if isinstance(exc, A2ACallCancelled):
            return StepOutcome(state=StepState.CANCELLED, port="cancelled", skip_edges=True)

        return StepOutcome(
            state=StepState.FAILED,
            output={},
            port="failure",
            error=str(exc),
        )


def _build_ctx_vars(ctx: RunContext) -> dict[str, Any]:
    return {
        "run_id": str(ctx.run_id),
        "workflow_id": str(ctx.workflow_id),
        "now_unix": __import__("time").time(),
    }
