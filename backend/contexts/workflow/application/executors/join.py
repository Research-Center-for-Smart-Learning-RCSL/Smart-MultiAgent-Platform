"""join executor — fan-in marker with strategy all/any/count.

ASYNC-9: arrival tracking via a Redis SET of incoming-edge ids rather than a
raw INCR counter. A branch step that is retried or re-delivered by Arq
traverses the *same* edge, so ``SADD`` is idempotent and the count reflects
distinct branches — never inflated hits.

OBS-5: a single atomic Lua script also makes the join fire exactly once per
fan-in for *every* mode. A ``fired`` marker (set with ``NX``) is the one-shot
latch — so ``any`` / ``count`` joins no longer re-fire downstream for each
extra branch that arrives — while the epoch is only bumped once the fan-in is
fully drained (all incoming branches seen), keeping each loop pass isolated.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.models import (
    JoinMode,
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)

logger = logging.getLogger(__name__)

# Atomic fan-in arrival. In one indivisible step it:
#   1. registers this branch in the current epoch's SET (idempotent on retry);
#   2. claims the one-shot `fired` latch (SET NX) once `fire_threshold` distinct
#      branches have arrived — exactly one caller wins, so downstream runs once;
#   3. once *all* `total_branches` have arrived, drains the SET + latch and
#      bumps the epoch so the next loop pass through this join starts clean.
#   ARGV: run_id, node_id, branch_id, fire_threshold, total_branches, ttl_seconds
#   returns: {arrivals, is_finalizer}
_JOIN_ARRIVE_LUA = """
local epoch_key = 'wf:join:' .. ARGV[1] .. ':' .. ARGV[2] .. ':epoch'
local epoch = redis.call('GET', epoch_key)
if not epoch then epoch = '0' end
local set_key = 'wf:join:' .. ARGV[1] .. ':' .. ARGV[2] .. ':' .. epoch
local fired_key = set_key .. ':fired'
redis.call('SADD', set_key, ARGV[3])
redis.call('EXPIRE', set_key, ARGV[6])
local arrivals = redis.call('SCARD', set_key)
local is_finalizer = 0
if arrivals >= tonumber(ARGV[4]) then
    if redis.call('SET', fired_key, '1', 'NX', 'EX', ARGV[6]) then
        is_finalizer = 1
    end
end
if arrivals >= tonumber(ARGV[5]) then
    redis.call('DEL', set_key)
    redis.call('DEL', fired_key)
    redis.call('INCR', epoch_key)
    redis.call('EXPIRE', epoch_key, ARGV[6])
end
return {arrivals, is_finalizer}
"""

# 24 h — long enough for slow parallel branches, short enough to self-clean a
# join that genuinely stalls (e.g. an upstream branch that never executes).
_JOIN_TTL_SECONDS = 86_400


@register(NodeType.JOIN)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    from shared_kernel.auth.clients import get_redis

    config = node.config
    mode = JoinMode(config.get("mode", "all"))
    required_count = int(config.get("count", 1))

    # Count expected incoming edges to this join node from the workflow definition.
    edges = ctx.workflow_def.get("edges", [])
    incoming_count = sum(1 for e in edges if e.get("to") == node.id)
    total_branches = max(incoming_count, 1)

    # fire_threshold — how many distinct branches must arrive before the join
    # fires downstream (once). total_branches — how many must arrive before the
    # fan-in is fully drained and the epoch advances.
    if mode == JoinMode.ANY:
        fire_threshold = 1
    elif mode == JoinMode.COUNT:
        fire_threshold = required_count
    else:  # ALL
        fire_threshold = total_branches

    # ASYNC-9: dedupe arrivals by the incoming-edge id. A retried / re-delivered
    # branch step traverses the same edge, so SADD is idempotent and the count
    # reflects *distinct* branches. Fall back to the node id when the edge is
    # unknown (abnormal topology — e.g. join reached without an edge).
    branch_id = ctx.arrived_via or node.id

    redis = get_redis()
    raw = await redis.eval(
        _JOIN_ARRIVE_LUA,
        0,
        str(ctx.run_id),
        node.id,
        branch_id,
        str(fire_threshold),
        str(total_branches),
        str(_JOIN_TTL_SECONDS),
    )
    arrivals = int(raw[0])
    is_finalizer = bool(int(raw[1]))

    logger.debug(
        "run %s: join node %s arrival %d (fire>=%d, total=%d) via %s (mode=%s, finalizer=%s)",
        ctx.run_id,
        node.id,
        arrivals,
        fire_threshold,
        total_branches,
        branch_id,
        mode.value,
        is_finalizer,
    )

    if not is_finalizer:
        # Either not enough branches yet, or the join already fired for this
        # fan-in. Succeed the step but do NOT follow outgoing edges — skip_edges
        # keeps the engine from advancing past the join more than once.
        return StepOutcome(
            state=StepState.SUCCEEDED,
            output={"arrivals": arrivals, "required": fire_threshold, "mode": mode.value},
            skip_edges=True,
        )

    # This branch won the one-shot latch — fire the join exactly once.
    return StepOutcome(
        state=StepState.SUCCEEDED,
        output={"mode": mode.value, "arrivals": arrivals, "required": fire_threshold},
        port="default",
    )
