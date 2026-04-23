"""subagent_spawn executor — create child agent(s) under a parent (§15.6)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.sel.template import interpolate
from contexts.workflow.application.executors.registry import register


@register(NodeType.SUBAGENT_SPAWN)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    parent_agent_id = config.get("parent_agent_id", "")
    task_template = config.get("task_template", "")
    output_variable = config.get("output_variable")
    max_alive = config.get("max_alive_simultaneously", 3)

    variables = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": {"run_id": str(ctx.run_id), "workflow_id": str(ctx.workflow_id)},
    }
    task_desc = interpolate(task_template, variables)

    try:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)
        instance = await facade.spawn_subagent(
            parent_instance_id=ctx.run_id,
            parent_agent_id=uuid.UUID(parent_agent_id),
            task_description=task_desc,
            max_concurrent=max_alive,
        )

        result = {"instance_id": str(instance.id)}
        if output_variable:
            ctx.variables[output_variable] = str(instance.id)

        if config.get("wait_for_all", True):
            return StepOutcome(
                state=StepState.RUNNING,
                output=result,
                port="success",
                park=True,
            )

        return StepOutcome(
            state=StepState.SUCCEEDED,
            output=result,
            port="success",
        )

    except Exception as exc:
        return StepOutcome(
            state=StepState.FAILED,
            output={},
            port="failure",
            error=str(exc),
        )
