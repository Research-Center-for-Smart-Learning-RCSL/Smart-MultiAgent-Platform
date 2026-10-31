"""Message / presence → wake-up trigger dispatch (K.3, link a).

The conversation context reacts to two events by asking the orchestration
context whether any room-bound agent should wake:

- a **message** was created in a room (``evaluate_message_wakeups``) — drives
  the ``every_n_messages`` trigger (R15.01);
- a **user joined** a room (``evaluate_presence_change``) — re-arms the
  ``silence_minutes`` timer (R15.05b). The empty-room pause is handled by the
  live roster read in `evaluate_silence_trigger` instead
  (2026-07-27-wakeup-sweep-failure-isolation C2).

Both are pure evaluation glue: they list the room's bound agents and call the
orchestration facade, which owns the Redis counters/timers and the audit. They
do **not** enqueue the turn — the caller (web endpoint) enqueues a
``wakeup_agent`` arq job for each returned agent *after the message commits*, so
the worker's turn sees a durable row. Keeping the enqueue out of here keeps this
module free of any arq / app dependency and unit-testable in isolation.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import ChatroomAgent, ChatroomAgentRole
from contexts.conversation.infrastructure import room_joins
from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository
from shared_kernel.auth.clients import now

if TYPE_CHECKING:
    from contexts.orchestration.interfaces.facade import WakeTarget

_log = logging.getLogger(__name__)

__all__ = [
    "evaluate_creator_message",
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
    sender_is_human: bool | None = None,
) -> list[WakeTarget]:
    """Return the agents whose ``every_n_messages`` trigger fired for this
    message, each with the binding incarnation that fired ([R15.09]). Side
    effects (counter increment, autostop reset on user sends, silence-timer
    touch) happen inside the orchestration facade.

    ``sender_agent_id`` — set when the message was authored by a room-bound
    agent; used to exclude the author from its own wake list (R15.01/Q49).

    ``bound_agents`` lets the caller supply the already-fetched binding rows
    (see ``list_bound_agents``); when omitted they are queried here. The rows
    carry the role so observer bindings are flagged to orchestration
    (O-2/R28.04). Returns an empty list when no agent is bound to the room.

    ``sender_is_human`` overrides ``sender_is_user`` for ``count: "human"``
    ([R15.01]); only a caller whose message resets autostop without being written
    by a person (a released observation) needs to pass it.
    """
    rows = bound_agents if bound_agents is not None else await ChatroomAgentRepository(db).list(chatroom_id)
    if not rows:
        return []
    agent_ids = [a.agent_id for a in rows]
    observer_ids = {a.agent_id for a in rows if a.role is ChatroomAgentRole.OBSERVER}
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    extra = {} if sender_is_human is None else {"sender_is_human": sender_is_human}
    # Rows built by `list_bound_agents` already carry their wake-up layers; a row
    # built elsewhere without them makes orchestration read the room's once.
    layers = [r.wakeup for r in rows]
    states = [s for s in layers if s is not None] if all(s is not None for s in layers) else None
    return await OrchestrationFacade(db).on_message_created(
        room_id=chatroom_id,
        sender_is_user=sender_is_user,
        sender_agent_id=sender_agent_id,
        agent_ids=agent_ids,
        observer_agent_ids=observer_ids,
        states=states,
        **extra,
    )


async def evaluate_creator_message(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    sender_user_id: uuid.UUID,
) -> list[WakeTarget]:
    """The agents a message from the room creator wakes ([R15.29]); none for anyone else.

    Evaluated beside :func:`evaluate_message_wakeups`, which still counts the
    message: the caller wakes each returned agent for this message in place of
    its ``every_n_messages`` wake.
    """
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    return await OrchestrationFacade(db).on_creator_message(room_id=chatroom_id, sender_id=sender_user_id)


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
    principal_id: uuid.UUID,
) -> list[WakeTarget]:
    """A principal's first live connection to the room ([R15.02], [R13.79], [R15.26]).

    Re-arms the silence timer for every bound agent whoever else is connected,
    and records the arrival. Returns the ``on_join`` wakes a new arrival owes; a
    reconnection inside the principal's join window is not one and owes none.
    Like the message path, the caller enqueues them.

    The empty-room half of this call was retired
    (2026-07-27-wakeup-sweep-failure-isolation C2): the live roster read in
    `evaluate_silence_trigger` is the sole, level-triggered authority on an
    empty room now, so there is no longer a pause edge to deliver here.
    """
    try:
        is_new = await room_joins.record_join(chatroom_id, principal_id, at=now())
    except Exception:
        # [R13.79]: no arrival record is a missing fact, never a failed connection.
        _log.warning("join record failed for room %s", chatroom_id, exc_info=True)
        is_new = False
    await _re_arm_silence(db, chatroom_id=chatroom_id)
    if not is_new:
        return []
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    return await OrchestrationFacade(db).on_participant_joined(room_id=chatroom_id, principal_id=principal_id)


async def evaluate_room_activity(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
) -> None:
    """Re-arm the silence timer because the room is being worked in ([R15.02]).

    For room activity that is **not** a message the agents are waiting on -- today,
    an activity submission. A submission writes a transcript-visible row, so the
    silence trigger's "no room activity for T minutes" must account for it, or an
    agent configured to speak on a lull barges into a class that is busy filling in
    a worksheet.

    Deliberately narrower than :func:`evaluate_message_wakeups`: it does **not**
    count toward ``every_n_messages`` and does not reset the autostop cap. Counting
    submissions would wake every ``n=1`` agent once per submission -- one agent turn
    per student, on the operator's own provider key -- which is worse than the
    defect this fixes. Nor may a caller invoke ``evaluate_message_wakeups`` and
    discard its result as a middle ground: that still increments the message
    counter and drifts every ``n > 1`` agent off its cadence.

    Returns nothing because there is nothing to enqueue: re-arming a clock is the
    whole effect.
    """
    await _re_arm_silence(db, chatroom_id=chatroom_id)


async def evaluate_activation_change(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
) -> None:
    """An activity round started or ended in this room ([R15.02], [R15.03]).

    Room activity like a submission, so it re-arms silence and counts toward no
    ``every_n_messages``. Unlike a submission it also resets the autostop cap: one
    happens per stage of the class rather than per student, so the cost argument
    in :func:`evaluate_room_activity` does not apply.
    """
    await _re_arm_silence(db, chatroom_id=chatroom_id)
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    await OrchestrationFacade(db).on_activation_changed(room_id=chatroom_id)


async def _re_arm_silence(db: AsyncSession, *, chatroom_id: uuid.UUID) -> None:
    """Touch every bound agent's silence timestamp for this room.

    Observers included: the silence trigger runs for them too, and an observer is
    exactly the kind of agent that reports on a room it never speaks in.
    """
    agents = await ChatroomAgentRepository(db).list(chatroom_id)
    agent_ids = [a.agent_id for a in agents]
    if not agent_ids:
        return
    # Imported here rather than at module scope: the orchestration facade reaches
    # back into this context, and a module-level import closes the cycle at startup.
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    await OrchestrationFacade(db).on_users_present(
        room_id=chatroom_id,
        agent_ids=agent_ids,
    )
