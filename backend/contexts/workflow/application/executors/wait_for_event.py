"""wait_for_event executor — park branch until a matching event arrives.

W6: registers the wait context in Redis so external event dispatchers know which
run+node to resume, and schedules a delayed timeout task via the engine's
pending-enqueue mechanism so the run is not parked indefinitely.

Event dispatchers should:
  1. Read  wf:wait:by_event:{event_type}  (a Redis SET) for waiting runs.
  2. Call  engine.resume_at_port(run_id, node_id, "default")  when the event fires.
  3. Clean up the Redis keys (wf:wait:{run_id}:{node_id} and the SET member).
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)

logger = logging.getLogger(__name__)

_TIMEOUT_TASK = "workflow_event_timeout"


@register(NodeType.WAIT_FOR_EVENT)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    from shared_kernel.auth.clients import get_redis

    config = node.config
    event_type = config.get("event_type", "timer")
    timeout_seconds = int(config.get("timeout_seconds", 600))

    redis = get_redis()

    # Register wait context so event dispatchers can locate this parked run.
    wait_key = f"wf:wait:{ctx.run_id}:{node.id}"
    await redis.set(
        wait_key,
        json.dumps(
            {
                "run_id": str(ctx.run_id),
                "node_id": node.id,
                "event_type": event_type,
            }
        ),
        ex=timeout_seconds + 60,  # keep a grace window after timeout
    )

    # Index by event_type for O(1) lookup by dispatchers.
    index_key = f"wf:wait:by_event:{event_type}"
    await redis.sadd(index_key, f"{ctx.run_id}:{node.id}")
    await redis.expire(index_key, timeout_seconds + 60)

    logger.info(
        "run %s: waiting for event %r at node %s (timeout=%ds)",
        ctx.run_id,
        event_type,
        node.id,
        timeout_seconds,
    )

    # Return park=True + timeout_ms/timeout_task so the engine enqueues a delayed
    # Arq task (workflow_event_timeout) that fires if the event never arrives.
    return StepOutcome(
        state=StepState.RUNNING,
        output={"event_type": event_type, "timeout_seconds": timeout_seconds},
        port="default",
        park=True,
        timeout_ms=timeout_seconds * 1_000,
        timeout_task=_TIMEOUT_TASK,
    )
