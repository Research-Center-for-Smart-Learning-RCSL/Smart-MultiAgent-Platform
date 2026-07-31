"""Finding turns that started and never finished.

A turn owns its own cleanup on both failure paths, but nothing survives a
``SIGKILL`` — the process is gone before any ``except`` or ``finally`` runs, and
``wakeup_agent`` is registered ``max_tries=1`` so nothing re-runs it either. The
only durable trace such a turn leaves is its committed ``agent.turn_started``
audit row with no matching finish, which is what this module finds.

Scope: room turns only. The headless A2A path (``run_input_turn``) audits its
start with no ``chatroom_id`` in the metadata, so it is filtered out here — it
has no room channel to notify and no coalesced trigger to drain, and its caller
is a live request that observes the failure itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from shared_kernel.audit import audit_logs

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

TURN_STARTED = "agent.turn_started"
TURN_FINISHED = "agent.turn_finished"
TURN_FAILED = "agent.turn_failed"

_FINISH_ACTIONS = (TURN_FINISHED, TURN_FAILED)


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """One ``agent.turn_*`` audit row, reduced to what pairing needs."""

    agent_id: uuid.UUID
    chatroom_id: uuid.UUID
    action: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StrandedTurn:
    agent_id: uuid.UUID
    chatroom_id: uuid.UUID
    started_at: datetime


def stranded_from_events(events: Sequence[TurnEvent], *, deadline: datetime) -> list[StrandedTurn]:
    """Pair starts with finishes per (agent, room) and return the unmatched ones.

    Pairing rather than "is there any later finish": an agent that stranded one
    turn and then completed the next would otherwise look resolved, because the
    second turn's finish row also sits after the first turn's start. The turn
    lock guarantees at most one turn per (agent, room) at a time, so a start
    immediately followed by another start — with no finish between — means the
    first one never finished.

    ``events`` must be ordered by ``created_at``. ``deadline`` is the youngest a
    start may be and still count as stranded; anything newer may simply still be
    running.
    """
    pending: dict[tuple[uuid.UUID, uuid.UUID], datetime] = {}
    stranded: list[StrandedTurn] = []
    for ev in events:
        key = (ev.agent_id, ev.chatroom_id)
        if ev.action == TURN_STARTED:
            previous = pending.get(key)
            if previous is not None and previous < deadline:
                stranded.append(StrandedTurn(ev.agent_id, ev.chatroom_id, previous))
            pending[key] = ev.created_at
        else:
            pending.pop(key, None)
    stranded.extend(
        StrandedTurn(agent_id, room, started_at)
        for (agent_id, room), started_at in pending.items()
        if started_at < deadline
    )
    stranded.sort(key=lambda s: s.started_at)
    return stranded


def turn_events_query(*, horizon: datetime, cap: int) -> sa.Select[Any]:
    """The sweep's one statement, built separately so a test can compile it.

    Two indexed range predicates (``action`` and ``created_at`` are both
    indexed) rather than a correlated ``NOT EXISTS`` per candidate: the pairing
    is cheap in Python, and this way the query shape does not depend on a
    metadata index that ``audit_logs`` does not have.

    ``cap + 1`` rows so the caller can tell a full read from a truncated one.
    """
    chatroom = audit_logs.c.metadata["chatroom_id"].astext
    return (
        sa.select(
            audit_logs.c.resource_id,
            chatroom.label("chatroom_id"),
            audit_logs.c.action,
            audit_logs.c.created_at,
        )
        .where(
            audit_logs.c.action.in_((TURN_STARTED, *_FINISH_ACTIONS)),
            audit_logs.c.resource_type == "agent",
            audit_logs.c.created_at >= horizon,
            chatroom.is_not(None),
        )
        .order_by(audit_logs.c.created_at, audit_logs.c.id)
        .limit(cap + 1)
    )


class StrandedTurnRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_turn_events(self, *, horizon: datetime, cap: int) -> tuple[list[TurnEvent], bool]:
        """Every room turn's start/finish rows since ``horizon``, oldest first.

        Returns ``(events, truncated)``. A truncated read is reported rather
        than silently trimmed — the caller logs it, because a capped sweep
        covers less than it looks like it does.
        """
        rows = (await self._db.execute(turn_events_query(horizon=horizon, cap=cap))).all()
        return [ev for r in rows[:cap] if (ev := _row_to_event(r)) is not None], len(rows) > cap


def _row_to_event(row: Any) -> TurnEvent | None:
    """Drop rows whose ids are unusable rather than failing the whole sweep.

    ``chatroom_id`` comes out of JSONB, so it is only a string by convention;
    one malformed value must not cost every other stranded turn its cleanup.
    """
    if row.resource_id is None:
        return None
    try:
        chatroom_id = uuid.UUID(row.chatroom_id)
    except (ValueError, TypeError, AttributeError):
        return None
    return TurnEvent(
        agent_id=row.resource_id,
        chatroom_id=chatroom_id,
        action=row.action,
        created_at=row.created_at,
    )


__all__ = [
    "TURN_FAILED",
    "TURN_FINISHED",
    "TURN_STARTED",
    "StrandedTurn",
    "StrandedTurnRepository",
    "TurnEvent",
    "stranded_from_events",
    "turn_events_query",
]
