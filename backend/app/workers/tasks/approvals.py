"""Arq tasks for approval-gate participation (K.3 follow-up).

``drive_approver_turn`` closes the loop ``_notify_and_arm`` left open: a
pending-notify is only drained at the approver's *next* turn, and nothing
else causes one for a headless (non-room-bound) approver — so every workflow
approval gate used to fall to the timeout port. This task runs one headless
input turn per approver; draining the parked ``approval_request`` note
supplies the ``cast_approval_vote`` tool for exactly the pending gate
(``turn_engine._pending_context_and_tools``).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from loguru import logger

from shared_kernel.db.session import async_session

_APPROVER_TURN_INPUT = "An approval request is pending; review your notifications and vote."

# The gate is created inside the caller's (executor's) transaction, so a freshly
# dispatched job can race the commit and see no approval row. Distinguish that
# (retry within a small budget) from a genuinely-resolved gate (skip).
_NOT_VISIBLE_RETRY_DELAY_S = 2
_NOT_VISIBLE_MAX_ATTEMPTS = 5


async def approval_gate_announce(
    ctx: dict[str, Any],
    approval_id: str,
    chatroom_id: str | None = None,
    node_id: str | None = None,
    question: str | None = None,
    attempt: int = 0,
) -> str:
    """Post-commit announcement of a created approval gate (F-18).

    Enqueued once, pre-commit, by ``ApprovalService.create_gate``. Opens its own
    session, re-reads the row, and only then performs every externally-visible
    effect (WS publishes, approver notifies, timeout arm, approver-turn
    dispatch) — so nothing escapes before the row is durable. An enqueue orphaned
    by a rolled-back creation re-reads, finds no row, and gives up harmlessly.
    Retries within a small budget when the row is not yet visible (the creation
    transaction has not committed).
    """
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    aid = uuid.UUID(approval_id)
    cid = uuid.UUID(chatroom_id) if chatroom_id else None
    async with async_session() as db:
        announced = await OrchestrationFacade(db).announce_approval_gate(
            aid,
            chatroom_id=cid,
            node_id=node_id,
            question=question,
        )
    if announced:
        return "announced"
    # Not yet visible — retry within budget rather than dropping the gate's
    # announcement (which would leave no card, no approver note and no armed
    # timeout).
    if attempt < _NOT_VISIBLE_MAX_ATTEMPTS:
        await ctx["redis"].enqueue_job(
            "approval_gate_announce",
            approval_id,
            chatroom_id,
            node_id,
            question,
            attempt + 1,
            _defer_by=timedelta(seconds=_NOT_VISIBLE_RETRY_DELAY_S),
        )
        return "retry:not_visible"
    logger.bind(approval_id=approval_id).warning("approval announce gave up: approval never became visible")
    return "noop:gone"


async def drive_approver_turn(
    ctx: dict[str, Any],
    agent_id: str,
    approval_id: str,
    chatroom_id: str | None = None,
    attempt: int = 0,
) -> str:
    """Run a headless turn for one approver of a pending gate.

    Enqueued by ``ApprovalService._notify_and_arm`` (one job per approver).
    Guards before spending a provider call: skip when the approval is already
    resolved (a fast co-approver vote / single mode may settle the gate before
    every job runs); retry within a small budget when the row is not yet visible
    (the create_gate transaction has not committed). The turn's commit / rollback
    and ``agent.turn_*`` audits are owned by the engine, mirroring ``wakeup_agent``.
    ``chatroom_id`` is the authoritative (server-side) room the vote is threaded
    back to; it scopes the approver's room-scoped Concept Map resolution for this
    turn (R11.09/R11.14) and rides along for log context.
    """
    from app.config.settings import get_settings
    from contexts.agents.application.runtime.turn_engine import TurnEngine
    from contexts.orchestration.domain.models import ApprovalState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    aid = uuid.UUID(agent_id)
    apid = uuid.UUID(approval_id)

    async with async_session() as db:
        approval = await OrchestrationFacade(db).get_approval(apid)
        if approval is None:
            # Not yet committed — retry within budget rather than dropping the
            # approver (which would leave the gate to fall to its timeout port).
            if attempt < _NOT_VISIBLE_MAX_ATTEMPTS:
                await ctx["redis"].enqueue_job(
                    "drive_approver_turn",
                    agent_id,
                    approval_id,
                    chatroom_id,
                    attempt + 1,
                    _defer_by=timedelta(seconds=_NOT_VISIBLE_RETRY_DELAY_S),
                )
                return "retry:not_visible"
            logger.bind(agent_id=agent_id, approval_id=approval_id, room_id=chatroom_id).warning(
                "approver turn gave up: approval never became visible"
            )
            return "skipped:not_visible"
        if approval.state != ApprovalState.PENDING:
            logger.bind(agent_id=agent_id, approval_id=approval_id, room_id=chatroom_id).info(
                "approver turn skipped: approval not pending"
            )
            return "skipped:not_pending"

        settings = get_settings()
        engine = TurnEngine(
            db,
            qdrant_url=settings.qdrant.url,
            qdrant_api_key=settings.qdrant.api_key,
            bge_reranker_url=settings.knowledge.bge_reranker_url,
        )
        result = await engine.run_input_turn(
            agent_id=aid,
            input_text=_APPROVER_TURN_INPUT,
            chatroom_id=uuid.UUID(chatroom_id) if chatroom_id else None,
        )

    bound = logger.bind(
        event="approver_turn_driven",
        agent_id=agent_id,
        approval_id=approval_id,
        room_id=chatroom_id,
        result=result.status,
        reason=result.reason,
    )
    if result.status != "completed":
        # This task exists to stop gates falling to the timeout port (see the
        # module docstring), and a turn that never reached the provider casts no
        # vote — so the gate does exactly that, minutes or hours later, with
        # nothing at info level to connect the two. There is no abstain signal to
        # send the gate yet (FU-5); until there is, at least make the cause
        # findable at the moment it happens rather than at the timeout.
        bound.warning("approver turn cast no vote; gate will wait for its timeout")
    elif result.approvals_voted < 1:
        # F-29 — a turn that reached the provider and completed is not the same
        # as one that voted: the model may answer without calling
        # cast_approval_vote. Previously indistinguishable from a real vote at
        # info level; the ballot itself is re-armed by the engine
        # (_settle_pending_approvals) while the gate is still PENDING.
        bound.warning("approver turn completed without casting a vote; gate will wait for its timeout")
    else:
        bound.info("approver turn driven")
    return result.status


__all__ = ["approval_gate_announce", "drive_approver_turn"]
