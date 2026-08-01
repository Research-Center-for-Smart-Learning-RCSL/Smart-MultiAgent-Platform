"""Arq cron task: resolve agent turns that started and never finished.

A turn cleans up after itself on both of its failure paths, including the
cancellation one, so the ordinary job timeout no longer strands anything. What
no in-process handler can survive is a ``SIGKILL`` — an OOM kill, a container
stop past its grace period, a hard node failure. The room is then left
"thinking" forever, the `agent.turn_started` audit row never gets its partner,
and any trigger that landed mid-turn sits parked for its full TTL with nobody
to answer the message that parked it. ``wakeup_agent`` is registered
``max_tries=1`` (a turn is not retry-safe), so nothing re-runs it either.

This sweep is the only cleanup on that path. Arq's cron lock keeps it singleton
across replicas; writing the missing ``agent.turn_failed`` row is what makes it
idempotent, since a resolved turn no longer matches.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from app.workers.tasks.orchestration import WAKEUP_TURN_TIMEOUT_S

# A turn is stranded only once it can no longer legitimately be running. The
# scoped job timeout is that bound; the margin covers the cleanup budget and
# clock skew between the app and the DB that stamps `created_at`.
STRANDED_TURN_BUDGET_S = WAKEUP_TURN_TIMEOUT_S + 120
# How far back a single sweep reads. Several budgets' worth, so a sweep that
# fails or is capped does not permanently lose the turns it did not reach, but
# bounded so the scan never grows with the age of the audit trail.
STRANDED_TURN_HORIZON_S = STRANDED_TURN_BUDGET_S * 6
# Rows per sweep. A cap that is hit is logged, never silently trimmed.
STRANDED_TURN_ROW_CAP = 5000

_REAPED_ERROR_KIND = "stranded"


async def agent_turn_reaper(ctx: dict[str, Any]) -> str:
    """Resolve every room turn whose start has no finish past the budget."""
    _ = ctx
    from contexts.agents.application.runtime.turn_engine import drain_queued_trigger
    from contexts.agents.infrastructure.stranded_turns import (
        TURN_FAILED,
        StrandedTurnRepository,
        stranded_from_events,
    )
    from contexts.conversation.interfaces import emit_agent_finished_error
    from shared_kernel import audit
    from shared_kernel.db.session import async_session

    now = datetime.now(UTC)
    deadline = now - timedelta(seconds=STRANDED_TURN_BUDGET_S)
    horizon = now - timedelta(seconds=STRANDED_TURN_HORIZON_S)
    reaped = 0

    async with async_session() as db:
        events, truncated = await StrandedTurnRepository(db).list_turn_events(
            horizon=horizon, cap=STRANDED_TURN_ROW_CAP
        )
        if truncated:
            logger.bind(cap=STRANDED_TURN_ROW_CAP).warning(
                "turn reaper: row cap hit, this sweep covers only the oldest window"
            )
        stranded = stranded_from_events(events, deadline=deadline)

        for turn in stranded:
            log = logger.bind(agent_id=str(turn.agent_id), room_id=str(turn.chatroom_id))
            try:
                # The audit row first, and committed: it is the record, and it
                # is also what stops the next sweep reaping this turn again.
                await audit.emit(
                    db,
                    audit.AuditEvent(
                        action=TURN_FAILED,
                        resource_type="agent",
                        resource_id=turn.agent_id,
                        metadata={
                            "chatroom_id": str(turn.chatroom_id),
                            "error": _REAPED_ERROR_KIND,
                            "reaped": True,
                            "started_at": turn.started_at.isoformat(),
                        },
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                log.exception("turn reaper: could not record the stranded turn")
                continue
            # Both best-effort and both already swallow their own transport
            # failures; neither may cost the next stranded turn its cleanup.
            await emit_agent_finished_error(turn.chatroom_id, turn.agent_id, _REAPED_ERROR_KIND)
            await drain_queued_trigger(turn.agent_id, turn.chatroom_id)
            reaped += 1
            log.warning("turn reaper: resolved a stranded turn")

    logger.bind(event="agent_turn_reaper_done", checked=len(events), reaped=reaped).info(
        f"turn reaper: resolved {reaped} stranded turn(s)"
    )
    return f"reaped={reaped}"


__all__ = [
    "STRANDED_TURN_BUDGET_S",
    "STRANDED_TURN_HORIZON_S",
    "STRANDED_TURN_ROW_CAP",
    "agent_turn_reaper",
]
