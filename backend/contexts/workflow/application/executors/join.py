"""join executor — fan-in marker with strategy all/any/count."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.domain.models import (
    JoinMode,
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.application.executors.registry import register


@register(NodeType.JOIN)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    mode = config.get("mode", "all")

    # The run engine tracks how many incoming branches have arrived.
    # This executor is invoked only once the join condition is met.
    # If the engine calls us, it means the strategy was satisfied.
    return StepOutcome(
        state=StepState.SUCCEEDED,
        output={"mode": mode},
        port="default",
    )
