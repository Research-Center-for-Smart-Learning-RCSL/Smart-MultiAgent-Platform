"""Message / presence → wake-up trigger dispatch (K.3, link a).

The conversation context reacts to two events by asking the orchestration
context whether any room-bound agent should wake:

- a **message** was created in a room (``evaluate_message_wakeups``) — drives
  the ``every_n_messages`` trigger (R15.01);
- room **presence** changed (``evaluate_presence_change``) — starts/pauses the
  ``silence_minutes`` timer (R15.05b).

Both are pure evaluation glue: they list the room's bound agents and call the
orchestration facade, which owns the Redis counters/timers and the audit. They
do **not** enqueue the turn — the caller (web endpoint) enqueues a
``wakeup_agent`` arq job for each returned agent *after the message commits*, so
the worker's turn sees a durable row. Keeping the enqueue out of here keeps this
module free of any arq / app dependency and unit-testable in isolation.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository

__all__ = [
    "evaluate_message_wakeups",
    "evaluate_presence_change",
    "filter_mentioned_bound_agents",
    "list_bound_agent_ids",
]


async def list_bound_agent_ids(db: AsyncSession, chatroom_id: uuid.UUID) -> list[uuid.UUID]:
    """List the agent ids bound to a room. Exposed so a caller evaluating more
    than one trigger for the same message (every_n + @mention) can fetch the
    binding once and pass it into both, instead of querying per evaluation."""
    return [a.agent_id for a in await ChatroomAgentRepository(db).list(chatroom_id)]


async def evaluate_message_wakeups(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    sender_is_user: bool,
    bound_agent_ids: list[uuid.UUID] | None = None,
) -> list[uuid.UUID]:
    """Return the agent ids whose ``every_n_messages`` trigger fired for this
    message. Side effects (counter increment, autostop reset on user sends,
    silence-timer touch) happen inside the orchestration facade.

    ``bound_agent_ids`` lets the caller supply an already-fetched room binding;
    when omitted it is queried here. Returns an empty list when no agent is
    bound to the room.
    """
    agent_ids = (
        bound_agent_ids if bound_agent_ids is not None else await list_bound_agent_ids(db, chatroom_id)
    )
    if not agent_ids:
        return []
    # Deferred import: orchestration → conversation has no cycle today, but the
    # function-local import keeps it impossible to introduce one accidentally.
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    return await OrchestrationFacade(db).on_message_created(
        room_id=chatroom_id,
        sender_is_user=sender_is_user,
        agent_ids=agent_ids,
    )


async def filter_mentioned_bound_agents(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    mention_agent_ids: list[uuid.UUID],
    bound_agent_ids: list[uuid.UUID] | None = None,
) -> list[uuid.UUID]:
    """Return the subset of ``mention_agent_ids`` actually bound to the room,
    preserving order and dropping duplicates. The send endpoint wakes each as
    an explicit call (bypassing wakeup-trigger evaluation), so this bound-check
    is the authorization boundary: a client can only summon agents the room
    already grants — never an arbitrary agent id.

    ``bound_agent_ids`` lets the caller supply an already-fetched room binding
    (shared with every_n evaluation) instead of re-querying. Returns an empty
    list when nothing matches or no agent is bound.
    """
    if not mention_agent_ids:
        return []
    bound = set(
        bound_agent_ids if bound_agent_ids is not None else await list_bound_agent_ids(db, chatroom_id)
    )
    out: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for agent_id in mention_agent_ids:
        if agent_id in bound and agent_id not in seen:
            seen.add(agent_id)
            out.append(agent_id)
    return out


async def evaluate_presence_change(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    has_live_users: bool,
) -> None:
    """Notify orchestration that room presence changed so silence timers for
    every bound agent are started (users present) or paused (room empty)."""
    agents = await ChatroomAgentRepository(db).list(chatroom_id)
    agent_ids = [a.agent_id for a in agents]
    if not agent_ids:
        return
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    await OrchestrationFacade(db).on_presence_changed(
        room_id=chatroom_id,
        agent_ids=agent_ids,
        has_live_users=has_live_users,
    )
