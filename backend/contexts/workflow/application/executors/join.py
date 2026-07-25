"""join executor — fan-in marker with strategy all/any/count.

ASYNC-9: arrival tracking via a Redis SET of incoming-edge ids rather than a
raw INCR counter. A branch step that is retried or re-delivered by Arq
traverses the *same* edge, so ``SADD`` is idempotent and the count reflects
distinct branches — never inflated hits.

OBS-5: a single atomic Lua script also makes the join fire exactly once per
fan-in for *every* mode. A ``fired`` marker (set with ``NX``) is the one-shot
latch — so ``any`` / ``count`` joins no longer re-fire downstream for each
extra branch that arrives.

Loop topologies (R14.01) route a back-edge into the same join that also
receives a fan-in. Arrival tracking is split into two independent tracks so
the two populations never share a counter:

- ``fan`` — fan-in edges only. Drains (and its epoch advances) once every
  fan-in edge has arrived, exactly as before back-edges existed.
- ``pass`` — back-edges only, fixed ``fire_threshold=1``: the first back-edge
  in a loop pass restarts the loop, and its one-shot latch suppresses any
  other back-edges arriving in the same pass. The join's configured ``mode``
  governs fan-in aggregation only, never loop continuation.

Each track has its own epoch, arrival SET and latch (``wf:join:{run}:{node}:
{track}:...``), so a back-edge arrival never touches the fan track's state.
A fan-in straggler that arrives after several loop passes have already run
is still evaluated against the same still-open fan epoch it always would
have been — see docs/tasks/2026-07-22-join-epoch-loop-reentry/spec.md §7 for
why a shared counter (an earlier draft of this fix) re-fires on that
straggler instead of suppressing it.
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
#      bumps the epoch so the next wave on this track starts clean.
#   ARGV: run_id, node_id, track, branch_id, fire_threshold, total_branches,
#         ttl_seconds
#   returns: {arrivals, is_finalizer}
#
# `track` ("fan" or "pass") is folded into every key so the fan-in count and
# the loop-pass count never share a counter — see the module docstring.
_JOIN_ARRIVE_LUA = """
local epoch_key = 'wf:join:' .. ARGV[1] .. ':' .. ARGV[2] .. ':' .. ARGV[3] .. ':epoch'
local epoch = redis.call('GET', epoch_key)
if not epoch then epoch = '0' end
local set_key = 'wf:join:' .. ARGV[1] .. ':' .. ARGV[2] .. ':' .. ARGV[3] .. ':' .. epoch
local fired_key = set_key .. ':fired'
redis.call('SADD', set_key, ARGV[4])
redis.call('EXPIRE', set_key, ARGV[7])
local arrivals = redis.call('SCARD', set_key)
local is_finalizer = 0
if arrivals >= tonumber(ARGV[5]) then
    if redis.call('SET', fired_key, '1', 'NX', 'EX', ARGV[7]) then
        is_finalizer = 1
    end
end
if arrivals >= tonumber(ARGV[6]) then
    redis.call('DEL', set_key)
    redis.call('DEL', fired_key)
    redis.call('INCR', epoch_key)
    redis.call('EXPIRE', epoch_key, ARGV[7])
end
return {arrivals, is_finalizer}
"""

# 24 h — long enough for slow parallel branches, short enough to self-clean a
# join that genuinely stalls (e.g. an upstream branch that never executes).
_JOIN_TTL_SECONDS = 86_400


def _classify_incoming_edges(node_id: str, edges: list[dict]) -> tuple[int, set[str]]:
    """Split node_id's incoming edges into a fan-in count and back-edge ids.

    An incoming edge is a back-edge when its source is reachable *from*
    node_id by following `edges` forward — i.e. the arrival closes a loop
    rather than feeding a fan-in wave. Plain forward reachability, same
    technique as the bounded walk in the linter (linter.py:602-604).
    """
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e.get("from"), []).append(e.get("to"))

    reachable: set[str] = set()
    stack = [node_id]
    while stack:
        current = stack.pop()
        for nxt in adjacency.get(current, []):
            if nxt not in reachable:
                reachable.add(nxt)
                stack.append(nxt)

    incoming = [e for e in edges if e.get("to") == node_id]
    back_edge_ids = {e["id"] for e in incoming if e.get("from") in reachable}
    fan_in_count = len(incoming) - len(back_edge_ids)
    return fan_in_count, back_edge_ids


@register(NodeType.JOIN)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    from shared_kernel.auth.clients import get_redis

    config = node.config
    mode = JoinMode(config.get("mode", "all"))
    required_count = int(config.get("count", 1))

    edges = ctx.workflow_def.get("edges", [])
    fan_in_count, back_edge_ids = _classify_incoming_edges(node.id, edges)
    total_fan_branches = max(fan_in_count, 1)
    total_back_edges = max(len(back_edge_ids), 1)

    # ASYNC-9: dedupe arrivals by the incoming-edge id. A retried / re-delivered
    # branch step traverses the same edge, so SADD is idempotent and the count
    # reflects *distinct* branches. Fall back to the node id when the edge is
    # unknown (abnormal topology — e.g. join reached without an edge).
    branch_id = ctx.arrived_via or node.id
    is_reentry = branch_id in back_edge_ids

    if is_reentry:
        # Q-8: any back-edge restarts the loop; the pass track's own latch
        # suppresses any other back-edges arriving in the same pass. Mode
        # governs fan-in aggregation only, never loop continuation.
        track = "pass"
        fire_threshold = 1
        total_branches = total_back_edges
    else:
        track = "fan"
        total_branches = total_fan_branches
        if mode == JoinMode.ANY:
            fire_threshold = 1
        elif mode == JoinMode.COUNT:
            fire_threshold = required_count
        else:  # ALL
            fire_threshold = total_fan_branches

    redis = get_redis()
    raw = await redis.eval(
        _JOIN_ARRIVE_LUA,
        0,
        str(ctx.run_id),
        node.id,
        track,
        branch_id,
        str(fire_threshold),
        str(total_branches),
        str(_JOIN_TTL_SECONDS),
    )
    arrivals = int(raw[0])
    is_finalizer = bool(int(raw[1]))

    logger.debug(
        "run %s: join node %s arrival %d (track=%s, fire>=%d, total=%d) via %s (mode=%s, finalizer=%s)",
        ctx.run_id,
        node.id,
        arrivals,
        track,
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
