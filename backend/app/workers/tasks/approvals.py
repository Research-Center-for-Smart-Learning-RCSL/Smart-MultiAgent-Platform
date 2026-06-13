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
from typing import Any

from loguru import logger

from shared_kernel.db.session import async_session

_APPROVER_TURN_INPUT = "An approval request is pending; review your notifications and vote."


async def drive_approver_turn(
    ctx: dict[str, Any],
    agent_id: str,
    approval_id: str,
    chatroom_id: str | None = None,
) -> str:
    """Run a headless turn for one approver of a pending gate.

    Enqueued by ``ApprovalService._notify_and_arm`` (one job per approver).
    Guards before spending a provider call: skip when the approval is gone or
    already resolved (a fast co-approver vote / single mode may settle the
    gate before every job runs). The turn's commit / rollback and
    ``agent.turn_*`` audits are owned by the engine, mirroring ``wakeup_agent``.
    ``chatroom_id`` rides along for log context only — the parked notify
    payload carries the room the vote is threaded back to.
    """
    from app.config.settings import get_settings
    from contexts.agents.application.runtime.turn_engine import TurnEngine
    from contexts.orchestration.domain.models import ApprovalState
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    aid = uuid.UUID(agent_id)
    apid = uuid.UUID(approval_id)

    async with async_session() as db:
        approval = await OrchestrationFacade(db).get_approval(apid)
        if approval is None or approval.state != ApprovalState.PENDING:
            logger.bind(agent_id=agent_id, approval_id=approval_id, room_id=chatroom_id).info(
                "approver turn skipped: approval not pending"
            )
            return "skipped:not_pending"

        settings = get_settings()
        engine = TurnEngine(
            db,
            qdrant_url=settings.qdrant.url,
            qdrant_api_key=settings.qdrant.api_key,
        )
        result = await engine.run_input_turn(agent_id=aid, input_text=_APPROVER_TURN_INPUT)

    logger.bind(
        event="approver_turn_driven",
        agent_id=agent_id,
        approval_id=approval_id,
        room_id=chatroom_id,
        result=result.status,
    ).info("approver turn driven")
    return result.status


__all__ = ["drive_approver_turn"]
