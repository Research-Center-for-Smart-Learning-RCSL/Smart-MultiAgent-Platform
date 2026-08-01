"""wait_for_event executor — park branch until a matching event arrives.

W6: registers the wait context in Redis so external event dispatchers know which
run+node to resume, and schedules a delayed timeout task via the engine's
pending-enqueue mechanism so the run is not parked indefinitely.

Event dispatchers MUST claim the wait atomically before resuming (ASYNC-10):
  1. Read   wf:wait:by_event:{event_type}  (a Redis SET) for waiting runs.
  2. GETDEL wf:wait:{run_id}:{node_id} — only the caller that receives a
     non-None payload owns the resume. This is the exact same claim that
     ``workflow_event_timeout`` makes, so an event and its timeout can never
     both resume the run (no duplicate downstream steps).
  3. If — and only if — the claim succeeded, call
     engine.resume_at_port(run_id, node_id, "default") and SREM the
     wf:wait:by_event:{event_type} member.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.claim_ttl import WAIT_CLAIM_GRACE_S, initial_claim_ttl
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)

logger = logging.getLogger(__name__)

_TIMEOUT_TASK = "workflow_event_timeout"
_TIMER_RESUME_TASK = "workflow_event_resume"
_DEFAULT_DELAY_SECONDS = 60


@register(NodeType.WAIT_FOR_EVENT)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    from shared_kernel.auth.clients import get_redis

    config = node.config
    event_type = config.get("event_type", "timer")
    timeout_seconds = int(config.get("timeout_seconds", 600))
    is_timer = event_type == "timer"
    delay_seconds = int(config.get("delay_seconds", _DEFAULT_DELAY_SECONDS))
    # A timer's own elapse IS its event — there is no external producer for it,
    # so timeout_seconds (meant for an event that might never arrive) is inert
    # here and delay_seconds is the only deadline that means anything (Q-1).
    park_seconds = delay_seconds if is_timer else timeout_seconds

    redis = get_redis()

    # Register wait context so event dispatchers can locate this parked run.
    # The full node config travels with it so the dispatcher (K.4) can match an
    # incoming event against this wait's criteria (chatroom_id / sender_filter /
    # content_regex for message_in_room; target_agent_id / types for
    # a2a_message; expression for variable_matches) before claiming the resume.
    wait_key = f"wf:wait:{ctx.run_id}:{node.id}"
    await redis.set(
        wait_key,
        json.dumps(
            {
                "run_id": str(ctx.run_id),
                "node_id": node.id,
                "event_type": event_type,
                "match": dict(config),
            }
        ),
        ex=initial_claim_ttl(park_seconds, WAIT_CLAIM_GRACE_S),  # grace after the deadline
    )

    # Index by event_type for O(1) lookup by dispatchers.
    #
    # The index set is SHARED by every wait of this event_type, so its TTL must
    # only ever be *extended* — an unconditional EXPIRE here let a short wait
    # registered after a long one shrink the set's TTL and silently drop the
    # long wait from the dispatcher (K remediation). TTL-compare instead of
    # EXPIRE GT: GT requires Redis ≥ 7 while the rest of this module only
    # assumes ≥ 6.2 (GETDEL); the compare also covers a fresh key (TTL == -1
    # after SADD), which GT would leave persistent forever.
    index_key = f"wf:wait:by_event:{event_type}"
    index_ttl = initial_claim_ttl(park_seconds, WAIT_CLAIM_GRACE_S)
    await redis.sadd(index_key, f"{ctx.run_id}:{node.id}")
    current_ttl = await redis.ttl(index_key)
    if current_ttl < index_ttl:
        await redis.expire(index_key, index_ttl)

    logger.info(
        "run %s: waiting for event %r at node %s (park=%ds)",
        ctx.run_id,
        event_type,
        node.id,
        park_seconds,
    )

    # Return park=True + timeout_ms/timeout_task so the engine enqueues a delayed
    # Arq task that fires if the deadline elapses first. A timer wait has no
    # external producer, so its own delay elapsing IS the event — it resumes
    # via workflow_event_resume (the "default" port), not workflow_event_timeout
    # (the "timeout" port), which every other event type still uses (Q-1).
    return StepOutcome(
        state=StepState.RUNNING,
        output={"event_type": event_type, "timeout_seconds": timeout_seconds},
        port="default",
        park=True,
        timeout_ms=park_seconds * 1_000,
        timeout_task=_TIMER_RESUME_TASK if is_timer else _TIMEOUT_TASK,
    )
