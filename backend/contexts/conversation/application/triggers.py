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
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import ChatroomAgent, ChatroomAgentRole
from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository

__all__ = [
    "evaluate_message_wakeups",
    "evaluate_presence_change",
    "filter_mentioned_bound_agents",
    "list_bound_agents",
]


async def list_bound_agents(db: AsyncSession, chatroom_id: uuid.UUID) -> Sequence[ChatroomAgent]:
    """Fetch the room's binding rows (agent id + role) once. Exposed so a
    caller evaluating more than one trigger for the same message (every_n +
    @mention + GraphRAG) can share the read instead of querying per
    evaluation. Rows, not bare ids — ``evaluate_message_wakeups`` needs the
    role to exempt observers from the presence gate (O-2/R28.04)."""
    return await ChatroomAgentRepository(db).list(chatroom_id)


async def evaluate_message_wakeups(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    sender_is_user: bool,
    sender_agent_id: uuid.UUID | None = None,
    bound_agents: Sequence[ChatroomAgent] | None = None,
) -> list[uuid.UUID]:
    """Return the agent ids whose ``every_n_messages`` trigger fired for this
    message. Side effects (counter increment, autostop reset on user sends,
    silence-timer touch) happen inside the orchestration facade.

    ``sender_agent_id`` — set when the message was authored by a room-bound
    agent; used to exclude the author from its own wake list (R15.01/Q49).

    ``bound_agents`` lets the caller supply the already-fetched binding rows
    (see ``list_bound_agents``); when omitted they are queried here. The rows
    carry the role so observer bindings are flagged to orchestration
    (O-2/R28.04). Returns an empty list when no agent is bound to the room.
    """
    rows = bound_agents if bound_agents is not None else await ChatroomAgentRepository(db).list(chatroom_id)
    if not rows:
        return []
    agent_ids = [a.agent_id for a in rows]
    observer_ids = {a.agent_id for a in rows if a.role is ChatroomAgentRole.OBSERVER}
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    return await OrchestrationFacade(db).on_message_created(
        room_id=chatroom_id,
        sender_is_user=sender_is_user,
        sender_agent_id=sender_agent_id,
        agent_ids=agent_ids,
        observer_agent_ids=observer_ids,
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

    ``bound_agent_ids`` can narrow the candidate set further (caller-supplied
    superset from every_n evaluation), but the role check below always runs
    against a fresh role-aware fetch. Returns an empty list when nothing
    matches or no agent is bound.
    """
    if not mention_agent_ids:
        return []
    # R28.04: mentions may only summon normal-role bindings. Observer ids are
    # dropped silently — the same response as "not bound", so a probing client
    # cannot use mentions as an existence oracle. The role fetch is
    # unconditional (never trusts a caller-supplied list) so the gate cannot
    # be bypassed by a role-blind call site.
    rows = await ChatroomAgentRepository(db).list(chatroom_id)
    bound = {a.agent_id for a in rows if a.role is ChatroomAgentRole.NORMAL}
    if bound_agent_ids is not None:
        bound &= set(bound_agent_ids)
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
