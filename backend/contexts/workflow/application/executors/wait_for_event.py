"""wait_for_event executor — park branch until a matching event arrives."""

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


@register(NodeType.WAIT_FOR_EVENT)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    event_type = config.get("event_type", "timer")

    # All wait_for_event nodes park the branch.
    # The dispatcher watches Redis keys wf:wait:{run_id}:{event_name}
    # and resumes when a matching event fires.
    return StepOutcome(
        state=StepState.RUNNING,
        output={
            "event_type": event_type,
            "timeout_seconds": config.get("timeout_seconds", 600),
        },
        port="default",
        park=True,
    )
