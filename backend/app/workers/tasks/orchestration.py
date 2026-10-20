"""Arq tasks for the orchestration context (G.3 / G.5 / G.9).

Tasks:
- ``wakeup_agent``          — enqueue an agent wake-up for a specific room
- ``evaluate_silence``      — periodic check of silence triggers across rooms
- ``wakeup_refresh``        — periodic clear of due runtime wake-up layers ([R15.09])

G.9 audit wiring: ``make_dlq_audit_callback`` is used by the consumer loop
to emit ``a2a.dlq`` audit events whenever a message is moved to the DLQ.

M19 refactor: ``compact_chatroom`` moved to ``conversation.py`` (conversation
concern). ``evaluate_silence`` SQL queries extracted into a repository method.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from loguru import logger

from contexts.agents.infrastructure.turn_lock import DEFAULT_TURN_TTL_S
from shared_kernel.db.session import async_session

# The per-(agent, room) turn lock is the single-writer authority for a turn, not
# this timeout: it is heartbeat-refreshed for as long as the turn runs and fails
# closed on loss (the turn aborts at the next tool-round boundary). So the job
# timeout is only a runaway backstop and is given headroom over the lock TTL,
# mirroring `graphrag.py`'s GRAPHRAG_BUILD_TIMEOUT_S. It must not be tightened
# below the TTL: one provider stream read alone may take STREAM_TIMEOUT (300 s,
# `contexts/keys/infrastructure/adapters/base.py`), so a sub-TTL budget would
# kill healthy turns mid-stream.
WAKEUP_TURN_TIMEOUT_S = DEFAULT_TURN_TTL_S * 2


async def wakeup_agent(
    ctx: dict[str, Any],
    agent_id: str,
    room_id: str,
    trigger: str = "every_n_messages",
    trigger_message_id: str | None = None,
    binding_id: str | None = None,
) -> str:
    """Fire a wake-up for a single agent in a single room (K.3, link b).

    Enqueued by the wake-up evaluator (``every_n_messages`` from the message
    endpoint, ``silence_minutes`` from the silence sweep). Runs a real agent
    turn through the K.2 ``TurnEngine`` and records ``wakeup.fired`` *after*
    the turn. ``trigger_message_id`` pins the turn to the exact message that
    fired it (every_n_messages / mention), so attachment resolution doesn't
    race a fast follow-up message that becomes "latest" before this job runs.
    ``silence_minutes`` has no specific triggering message and always passes
    ``None`` here — the turn falls back to resolving against the room's
    current latest user message, which is correct for a time-based trigger.
    Guards before spending a provider call:

    - room still live (not soft-deleted);
    - agent still exists (``get_agent`` filters deleted);
    - autostop not tripped — consecutive agent-only rounds below the binding's
      effective cap (R15.03/R15.04), a backstop against turn storms for both
      trigger kinds (the silence sweep also checks this pre-enqueue);
    - for an autonomous trigger, the binding that fired is still the live one
      (``binding_id``; an unbind and rebind is a new incarnation, [R15.09]) and
      its effective config still has that trigger on.

    The turn's own commit / rollback and ``agent.turn_*`` audits are owned by
    the engine; on a completed turn we bump autostop so a runaway agent loop
    eventually stalls until a user speaks again.
    """
    from app.config.settings import get_settings
    from contexts.agents.application.runtime.turn_engine import TurnEngine
    from contexts.agents.interfaces.facade import AgentsFacade
    from contexts.conversation.infrastructure.repositories import ChatroomRepository
    from contexts.orchestration.domain.models import EXPLICIT_TRIGGERS
    from contexts.orchestration.infrastructure import wakeup_state
    from contexts.orchestration.interfaces.facade import OrchestrationFacade
    from shared_kernel import audit

    aid = uuid.UUID(agent_id)
    rid = uuid.UUID(room_id)

    async with async_session() as db:
        # --- guards -----------------------------------------------------
        room = await ChatroomRepository(db).get(rid)
        if room is None:
            logger.bind(agent_id=agent_id, room_id=room_id).info("wakeup skipped: room gone")
            return "skipped:room_gone"
        agent = await AgentsFacade(db).get_agent(aid)
        if agent is None:
            if trigger in EXPLICIT_TRIGGERS:
                # A worker-level guard (agent soft-deleted while still room-bound)
                # returns before the TurnEngine emits, so without this an @mention
                # — or an explicit creator release-wake (R28.07), which is the
                # same shape of explicit call and must not fail silently either
                # (this mirrors the autostop bypass a few lines below, which
                # already treats "release" the same as "mention") — to such an
                # agent would silently produce nothing.
                from contexts.conversation.interfaces import (
                    emit_agent_finished_error,
                )

                await emit_agent_finished_error(rid, aid, "agent_gone")
            logger.bind(agent_id=agent_id, room_id=room_id).info("wakeup skipped: agent gone")
            return "skipped:agent_gone"
        # A `mention` is an explicit user call (not an autonomous round), so it
        # bypasses the gates below — a user summoning the agent must always get
        # a reply even after the agent has stalled on consecutive self-rounds.
        # `release` (R28.07) is the same shape: the room creator explicitly woke
        # the agent to read a released observation.
        # The incarnation this turn runs for: its queued follow-up and its autostop
        # round belong to it, not to whatever binding is live when the turn ends.
        turn_binding: uuid.UUID | None = None
        if trigger in EXPLICIT_TRIGGERS:
            from contexts.conversation.interfaces.facade import ConversationFacade

            bound = await ConversationFacade(db).binding_wakeup_state(chatroom_id=rid, agent_id=aid)
            turn_binding = bound.binding_id if bound is not None else None
        else:
            current = await OrchestrationFacade(db).room_wakeup(agent_id=aid, room_id=rid)
            if current is None or (binding_id is not None and str(current.state.binding_id) != binding_id):
                logger.bind(agent_id=agent_id, room_id=room_id).info("wakeup skipped: binding gone")
                return "skipped:binding_gone"
            turn_binding = current.state.binding_id
            triggers = current.effective.config.triggers
            still_on = {
                "every_n_messages": triggers.every_n_messages.enabled,
                "silence_minutes": triggers.silence_minutes.enabled,
            }.get(trigger, True)
            if triggers.call_only.enabled or not still_on:
                logger.bind(agent_id=agent_id, room_id=room_id, trigger=trigger).info(
                    "wakeup skipped: trigger turned off since it fired"
                )
                return "skipped:trigger_off"
            # O-3 (R28.12): an observer binding uses its own, higher round cap —
            # autostop resets only on user messages, and the prime observer use
            # case is a room no human is currently speaking in.
            autostop_limit = triggers.silence_minutes.autostop_limit_for(is_observer=current.is_observer)
            autostop_count = await wakeup_state.get_autostop_count(aid, rid, current.state.binding_id)
            if autostop_count >= autostop_limit:
                logger.bind(agent_id=agent_id, room_id=room_id, autostop=autostop_count).info(
                    "wakeup skipped: autostop tripped"
                )
                return "skipped:autostop"

        # --- turn -------------------------------------------------------
        settings = get_settings()
        engine = TurnEngine(
            db,
            qdrant_url=settings.qdrant.url,
            qdrant_api_key=settings.qdrant.api_key,
            bge_reranker_url=settings.knowledge.bge_reranker_url,
        )
        mid: uuid.UUID | None = None
        if trigger_message_id:
            try:
                mid = uuid.UUID(trigger_message_id)
            except ValueError:
                # Degrade like the "no trigger message" case (silence_minutes)
                # rather than hard-failing the job over a malformed optional id.
                logger.bind(agent_id=agent_id, room_id=room_id).warning(
                    "wakeup_agent: malformed trigger_message_id {!r}, falling back to latest-attachment"
                    " resolution",
                    trigger_message_id,
                )
        # Arq's per-enqueue job id is the turn's idempotency key: it rides into
        # the reply row under a partial unique index, so a turn that somehow runs
        # twice (a lapsed lock, not a retry -- `max_tries=1` covers retries)
        # short-circuits before the provider call instead of posting a second
        # reply. `None` when the caller is not an arq job, which only tests are.
        result = await engine.run_turn(
            agent_id=aid,
            chatroom_id=rid,
            trigger=trigger,
            trigger_message_id=mid,
            turn_job_id=ctx.get("job_id"),
            binding_id=turn_binding,
        )

        # Result-accurate audit slug: `wakeup.fired` is reserved for turns that
        # actually produced a reply (mirrors the agent.turn_* naming).
        action = {
            "completed": "wakeup.fired",
            "skipped": "wakeup.skipped",
        }.get(result.status, "wakeup.failed")
        await audit.emit(
            db,
            audit.AuditEvent(
                action=action,
                resource_type="agent",
                resource_id=aid,
                metadata={
                    "room_id": str(rid),
                    "trigger": trigger,
                    "result": result.status,
                    "reason": result.reason,
                },
            ),
        )
        await db.commit()

        # Count the round for autostop only when a reply was actually produced.
        if result.status == "completed":
            await OrchestrationFacade(db).on_agent_message_sent(
                agent_id=aid, room_id=rid, binding_id=turn_binding
            )

    # K.4: fire a wakeup_signal to workflows whose dormant trigger watches this
    # agent. Best-effort, post-commit; failure must not fail the wake-up.
    try:
        await ctx["redis"].enqueue_job("workflow_signal", "wakeup", {"agent_id": str(aid)})
    except Exception:
        logger.bind(agent_id=agent_id).warning("wakeup workflow-signal dispatch failed")

    logger.bind(
        event="wakeup_fired",
        agent_id=agent_id,
        room_id=room_id,
        trigger=trigger,
        result=result.status,
    ).info("wakeup fired")
    return result.status


# The gate is armed inside the caller's (executor's) transaction, so the
# deadline can fire before that commit lands and observe no approval row.
# Distinguish that (retry within a small budget, mirroring drive_approver_turn)
# from a genuinely rolled-back / never-created gate (give up). handle_timeout is
# idempotent, so a spurious retry after a real rollback is harmless.
_TIMEOUT_NOT_VISIBLE_RETRY_DELAY_S = 2
_TIMEOUT_NOT_VISIBLE_MAX_ATTEMPTS = 5


async def approval_timeout(
    ctx: dict[str, Any],
    approval_id: str,
    chatroom_id: str | None = None,
    attempt: int = 0,
) -> str:
    """Resolve an approval gate that did not reach a verdict in time (R15.13).

    Armed as a deferred job when the gate is created (K.3). Idempotent: if the
    gate already resolved via votes, ``handle_timeout`` is a no-op and returns
    the existing state. When the row is not yet visible (creation transaction
    uncommitted), retries within a small budget rather than abandoning the
    gate's liveness backstop.
    """
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    aid = uuid.UUID(approval_id)
    cid = uuid.UUID(chatroom_id) if chatroom_id else None
    async with async_session() as db:
        state = await OrchestrationFacade(db).handle_approval_timeout(aid, chatroom_id=cid)
        await db.commit()
    if state is None:
        if attempt < _TIMEOUT_NOT_VISIBLE_MAX_ATTEMPTS:
            await ctx["redis"].enqueue_job(
                "approval_timeout",
                approval_id,
                chatroom_id,
                attempt + 1,
                _defer_by=timedelta(seconds=_TIMEOUT_NOT_VISIBLE_RETRY_DELAY_S),
            )
            return "retry:not_visible"
        logger.bind(approval_id=approval_id).info("approval timeout: approval gone")
        return "noop:gone"
    logger.bind(approval_id=approval_id, state=state.value).info("approval timeout handled")
    return state.value


async def evaluate_silence(ctx: dict[str, Any]) -> str:
    """Periodic sweep: fire silence_minutes wake-ups for room-bound agents.

    Runs every 30 s via Arq cron (G.3 / R15.02). Reads every live binding's
    stored wake-up layers through the conversation facade, so each is evaluated
    against its own room's effective config ([R15.09]). Each firing enqueues a
    ``wakeup_agent`` job carrying the binding id.

    After a firing the silence timestamp is reset so the trigger re-arms on
    the next T-minute window instead of re-firing on every 30 s sweep — an
    interim debounce until the Phase H agent runtime owns the timer.
    """
    from contexts.conversation.interfaces.facade import ConversationFacade
    from contexts.orchestration.application.wakeup_service import WakeupService
    from contexts.orchestration.infrastructure import wakeup_state

    redis = ctx["redis"]
    fired = 0
    checked = 0

    batch_size = 500
    async with async_session() as db:
        svc = WakeupService(db)
        conversation = ConversationFacade(db)
        # Paginate to avoid loading all bindings into memory at once.
        offset = 0
        states: list[Any] = []
        while True:
            batch = await conversation.live_binding_wakeup_states(limit=batch_size, offset=offset)
            states.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size

        for state in states:
            checked += 1
            try:
                if await svc.evaluate_silence_trigger(state):
                    # No trigger_message_id: silence is time-based, not tied to a
                    # specific message, so the turn intentionally falls back to
                    # resolving attachments against the room's current latest
                    # user message (wakeup_agent's default).
                    await redis.enqueue_job(
                        "wakeup_agent",
                        str(state.agent_id),
                        str(state.chatroom_id),
                        "silence_minutes",
                        None,
                        str(state.binding_id),
                    )
                    # Debounce: re-arm the timer so the next 30 s sweep does
                    # not immediately re-fire the same silence trigger.
                    await wakeup_state.touch_silence_timestamp(
                        state.agent_id, state.chatroom_id, state.binding_id
                    )
                    fired += 1
            except Exception:
                # One bad binding must not abort the sweep; clear any aborted
                # transaction so subsequent reads on this session succeed.
                await db.rollback()
                logger.bind(agent_id=str(state.agent_id), room_id=str(state.chatroom_id)).exception(
                    "evaluate_silence: trigger check failed"
                )

    logger.bind(event="silence_sweep_done", checked=checked, fired=fired).info(
        f"silence sweep: {fired}/{checked} bound agents woken"
    )
    return f"fired={fired}"


_REFRESH_BATCH = 200


async def wakeup_refresh(ctx: dict[str, Any]) -> str:
    """Periodic: clear each binding's runtime wake-up layer once it is due ([R15.09]).

    Runs hourly. Reads only bindings that hold a runtime layer, oldest refresh
    start first, in keyset pages; each is cleared when its designer's current
    ``refresh_every_hours`` has elapsed since that layer started. Human room
    overrides and other rooms are untouched, and no agent row is written.
    """
    from contexts.conversation.interfaces.facade import ConversationFacade
    from contexts.orchestration.application.room_wakeup_service import (
        RoomWakeupService,
        TimerEffects,
        apply_timer_effects,
    )

    refreshed = 0
    failed = 0
    # Applied only once the sweep's commit lands: a refresh whose savepoint or
    # commit fails must leave that binding's timers as they were.
    owed: list[TimerEffects | None] = []
    async with async_session() as db:
        svc = RoomWakeupService(db)
        conversation = ConversationFacade(db)
        after = None
        while True:
            page = await conversation.runtime_layer_binding_states(limit=_REFRESH_BATCH, after=after)
            for state in page:
                try:
                    # Each write isolated in its own SAVEPOINT: a bare rollback
                    # would discard every earlier binding's flushed refresh.
                    async with db.begin_nested():
                        change = await svc.refresh_if_due(state)
                    if change is not None:
                        refreshed += 1
                        owed.append(change.effects)
                except Exception:
                    failed += 1
                    logger.bind(binding_id=str(state.binding_id)).exception("wakeup refresh failed")
            if len(page) < _REFRESH_BATCH:
                break
            last = page[-1]
            if last.runtime_started_at is None:  # the page query selects only started layers
                break
            after = (last.runtime_started_at, last.binding_id)
        try:
            await db.commit()
        except Exception:
            logger.bind(event="wakeup_refresh_commit_failed", lost=refreshed, failed=failed).exception(
                "wakeup refresh sweep: commit failed, every refresh this sweep collected was lost"
            )
            return f"sweep_failed lost={refreshed} failed={failed}"
    try:
        await apply_timer_effects(owed)
    except Exception:
        logger.bind(event="wakeup_refresh_timers_failed").exception(
            "wakeup refresh sweep: committed, but resetting the refreshed bindings' timers failed"
        )

    logger.bind(event="wakeup_refresh_done", refreshed=refreshed, failed=failed).info(
        f"wakeup refresh sweep: {refreshed} bindings refreshed, {failed} failed"
    )
    return f"refreshed={refreshed} failed={failed}"


def make_dlq_audit_callback() -> Callable[[uuid.UUID, str, str, int], Awaitable[None]]:
    """Return a DlqCallback that emits the ``a2a.dlq`` audit event (G.9).

    The callback opens its own DB session so it is safe to call from the
    consumer loop which has no session of its own.
    """
    from shared_kernel import audit

    async def _on_dlq(
        agent_id: uuid.UUID,
        envelope_json: str,
        error: str,
        attempt: int,
    ) -> None:
        try:
            async with async_session() as db:
                await audit.emit(
                    db,
                    audit.AuditEvent(
                        action="a2a.dlq",
                        resource_type="a2a_message",
                        metadata={
                            "agent_id": str(agent_id),
                            "attempt": attempt,
                            "error": error[:500],
                        },
                    ),
                )
                await db.commit()
        except Exception:
            logger.bind(agent_id=str(agent_id)).exception("a2a.dlq audit failed")

    return _on_dlq


__all__ = [
    "WAKEUP_TURN_TIMEOUT_S",
    "approval_timeout",
    "evaluate_silence",
    "make_dlq_audit_callback",
    "wakeup_agent",
    "wakeup_refresh",
]
