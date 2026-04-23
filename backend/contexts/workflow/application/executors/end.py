"""end executor — terminal node, marks workflow run as success or failure."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.application.executors.registry import register


@register(NodeType.END)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    status = config.get("status", "success")
    return_vars = config.get("return_variables", [])

    return_data = {}
    for var_name in return_vars:
        return_data[var_name] = ctx.variables.get(var_name)

    return StepOutcome(
        state=StepState.SUCCEEDED if status == "success" else StepState.FAILED,
        output={"status": status, "return_variables": return_data},
        port="",
    )
