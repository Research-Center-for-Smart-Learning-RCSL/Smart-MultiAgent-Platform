"""instruct executor — send instruction to a target agent (§15.5)."""

from __future__ import annotations

import uuid

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


@register(NodeType.INSTRUCT)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    issuer_id = config.get("issuer_agent_id", "")
    target_id = config.get("target_agent_id", "")
    template = config.get("instruction_template", "")
    output_variable = config.get("output_variable")

    variables = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": {"run_id": str(ctx.run_id), "workflow_id": str(ctx.workflow_id)},
    }
    rendered = interpolate(template, variables)

    try:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)
        instruction = await facade.issue_instruct(
            issuer_agent_id=uuid.UUID(issuer_id),
            target_agent_id=uuid.UUID(target_id),
            payload={"instruction": rendered, "origin": "workflow"},
        )

        result_output = {"instruction_id": str(instruction.id)}

        if config.get("wait_for_completion", True):
            # Park until instruction completes
            return StepOutcome(
                state=StepState.RUNNING,
                output=result_output,
                port="success",
                park=True,
            )

        if output_variable:
            ctx.variables[output_variable] = str(instruction.id)

        return StepOutcome(
            state=StepState.SUCCEEDED,
            output=result_output,
            port="success",
        )

    except Exception as exc:
        return StepOutcome(
            state=StepState.FAILED,
            output={},
            port="failure",
            error=str(exc),
        )
