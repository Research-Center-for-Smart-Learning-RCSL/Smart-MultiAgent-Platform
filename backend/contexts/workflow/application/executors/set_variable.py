"""set_variable executor — compute SEL expressions and assign to workflow vars."""

from __future__ import annotations

import time
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
from contexts.workflow.sel.evaluator import evaluate


@register(NodeType.SET_VARIABLE)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    assignments = config.get("assignments", [])
    results: dict[str, Any] = {}

    sel_vars: dict[str, Any] = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": {
            "run_id": str(ctx.run_id),
            "workflow_id": str(ctx.workflow_id),
            "now_unix": time.time(),
        },
    }

    for assignment in assignments:
        var_name = assignment.get("variable", "")
        expression = assignment.get("expression", "")
        try:
            value = evaluate(expression, sel_vars)
            ctx.variables[var_name] = value
            sel_vars[var_name] = value
            results[var_name] = value
        except Exception as exc:
            return StepOutcome(
                state=StepState.FAILED,
                output=results,
                error=f"Failed to evaluate '{expression}' for '{var_name}': {exc}",
            )

    return StepOutcome(
        state=StepState.SUCCEEDED,
        output=results,
        port="default",
    )
