"""parallel executor — fan-out marker, all outgoing edges taken concurrently."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)


@register(NodeType.PARALLEL)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    # The run engine handles the actual fan-out by following ALL outgoing edges
    # from this node when port="default". This executor just marks success.
    return StepOutcome(
        state=StepState.SUCCEEDED,
        output={},
        port="default",
    )
