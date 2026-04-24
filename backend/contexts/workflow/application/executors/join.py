"""join executor — fan-in marker with strategy all/any/count.

W2: arrival tracking via Redis INCR so concurrent branch tasks (W1) coordinate
correctly across separate DB sessions without a race condition.
"""

from __future__ import annotations

import logging

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

logger = logging.getLogger(__name__)


@register(NodeType.JOIN)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    from shared_kernel.auth.clients import get_redis

    config = node.config
    mode = JoinMode(config.get("mode", "all"))
    required_count = int(config.get("count", 1))

    # Count expected incoming edges to this join node from the workflow definition.
    edges = ctx.workflow_def.get("edges", [])
    incoming_count = sum(1 for e in edges if e.get("to") == node.id)

    if mode == JoinMode.ANY:
        required = 1
    elif mode == JoinMode.COUNT:
        required = required_count
    else:  # ALL
        required = max(incoming_count, 1)

    # Atomic arrival counter — Redis INCR is single-operation and safe across
    # concurrent Arq tasks that each hold their own DB session.
    redis = get_redis()
    redis_key = f"wf:join:{ctx.run_id}:{node.id}"
    arrivals = await redis.incr(redis_key)
    # TTL = 24 h; long enough for slow parallel branches, short enough to self-clean.
    await redis.expire(redis_key, 86_400)

    logger.debug(
        "run %s: join node %s arrival %d/%d (mode=%s)",
        ctx.run_id, node.id, arrivals, required, mode.value,
    )

    if arrivals < required:
        # Not the last arrival — succeed the step but do NOT follow outgoing edges.
        # skip_edges prevents the engine from advancing past the join prematurely.
        return StepOutcome(
            state=StepState.SUCCEEDED,
            output={"arrivals": arrivals, "required": required, "mode": mode.value},
            skip_edges=True,
        )

    # Last (or only) arrival — clean up counter and proceed.
    await redis.delete(redis_key)

    return StepOutcome(
        state=StepState.SUCCEEDED,
        output={"mode": mode.value, "arrivals": arrivals},
        port="default",
    )
