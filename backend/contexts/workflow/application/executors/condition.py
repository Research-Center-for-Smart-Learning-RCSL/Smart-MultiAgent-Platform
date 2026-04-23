"""condition executor — evaluate up to 20 boolean branches."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.sel.evaluator import evaluate
from contexts.workflow.application.executors.registry import register


@register(NodeType.CONDITION)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    branches = config.get("branches", [])
    default_port = config.get("default_port", "default")

    variables: dict[str, Any] = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": {
            "run_id": str(ctx.run_id),
            "workflow_id": str(ctx.workflow_id),
            "now_unix": time.time(),
        },
    }

    for branch in branches:
        expr = branch.get("when", "")
        port = branch.get("port", "default")
        try:
            result = evaluate(expr, variables)
            if result:
                return StepOutcome(
                    state=StepState.SUCCEEDED,
                    output={"matched_port": port, "expression": expr},
                    port=port,
                )
        except Exception:
            continue

    return StepOutcome(
        state=StepState.SUCCEEDED,
        output={"matched_port": default_port, "expression": "(default)"},
        port=default_port,
    )
