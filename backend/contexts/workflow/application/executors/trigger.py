"""Trigger node executor — entry point of every workflow run."""

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


@register(NodeType.TRIGGER)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    return StepOutcome(
        state=StepState.SUCCEEDED,
        output={"trigger_type": node.config.get("trigger_type", "manual")},
        port="default",
    )
