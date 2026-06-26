"""Arq tasks for the orchestration context (G.3 / G.5 / G.9).

Tasks:
- ``wakeup_agent``          — enqueue an agent wake-up for a specific room
- ``evaluate_silence``      — periodic check of silence triggers across rooms
- ``wakeup_refresh``        — periodic snap of wakeup_config to authored values

G.9 audit wiring: ``make_dlq_audit_callback`` is used by the consumer loop
to emit ``a2a.dlq`` audit events whenever a message is moved to the DLQ.

M19 refactor: ``compact_chatroom`` moved to ``conversation.py`` (conversation
concern). ``evaluate_silence`` SQL queries extracted into a repository method.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from shared_kernel.db.session import async_session


async def _emit_mention_skip_notice(room_id: uuid.UUID, agent_id: uuid.UUID, reason: str) -> None:
    """Best-effort WS notice that an explicitly-summoned agent could not run.

    A worker-level guard (agent soft-deleted while still room-bound) returns
    before the TurnEngine ever emits, so without this an @mention to such an
    agent would silently produce nothing. Autonomous triggers are not routed
    here — only explicit mentions. Failure must never fail the wake-up job.

    Keyed on ``error`` (not ``reason``): the client only surfaces
    ``agent.finished.error`` — a ``reason`` payload is treated as a benign
    silent skip and never shown.
    """
    try:
        from contexts.conversation.infrastructure.channels import room_channel
        from shared_kernel.realtime.pubsub import Publisher

        await Publisher(room_channel(room_id)).emit(
            "agent.finished", {"error": reason, "agent_id": str(agent_id)}
        )
    except Exception:
        logger.bind(agent_id=str(agent_id), room_id=str(room_id)).warning("mention skip-notice emit failed")


async def wakeup_agent(
    ctx: dict[str, Any],
    agent_id: str,
    room_id: str,
    trigger: str = "every_n_messages",
) -> str:
    """Fire a wake-up for a single agent in a single room (K.3, link b).

    Enqueued by the wake-up evaluator (``every_n_messages`` from the message
    endpoint, ``silence_minutes`` from the silence sweep). Runs a real agent
    turn through the K.2 ``TurnEngine`` and records ``wakeup.fired`` *after*
    the turn. Guards before spending a provider call:

    - room still live (not soft-deleted);
    - agent still exists (``get_agent`` filters deleted);
    - autostop not tripped — consecutive agent-only rounds below the agent's
      ``autostop_rounds`` (R15.03/R15.04), a backstop against turn storms for
      both trigger kinds (the silence sweep also checks this pre-enqueue).

    The turn's own commit / rollback and ``agent.turn_*`` audits are owned by
    the engine; on a completed turn we bump autostop so a runaway agent loop
    eventually stalls until a user speaks again.
    """
    from app.config.settings import get_settings
    from contexts.agents.application.runtime.turn_engine import TurnEngine
    from contexts.agents.interfaces.facade import AgentsFacade
    from contexts.conversation.infrastructure.repositories import ChatroomRepository
    from contexts.orchestration.domain.models import WakeupConfig
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
            if trigger == "mention":
                await _emit_mention_skip_notice(rid, aid, "agent_gone")
            logger.bind(agent_id=agent_id, room_id=room_id).info("wakeup skipped: agent gone")
            return "skipped:agent_gone"
        cfg = WakeupConfig.from_dict(agent.wakeup_config)
        autostop_limit = cfg.triggers.silence_minutes.autostop_rounds
        autostop_count = await wakeup_state.get_autostop_count(aid, rid)
        # A `mention` is an explicit user call (not an autonomous round), so it
        # bypasses the autostop backstop — a user summoning the agent must always
        # get a reply even after the agent has stalled on consecutive self-rounds.
        if trigger != "mention" and autostop_limit > 0 and autostop_count >= autostop_limit:
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
        )
        result = await engine.run_turn(agent_id=aid, chatroom_id=rid, trigger=trigger)

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
            await OrchestrationFacade(db).on_agent_message_sent(agent_id=aid, room_id=rid)

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


async def approval_timeout(
    ctx: dict[str, Any],
    approval_id: str,
    chatroom_id: str | None = None,
) -> str:
    """Resolve an approval gate that did not reach a verdict in time (R15.13).

    Armed as a deferred job when the gate is created (K.3). Idempotent: if the
    gate already resolved via votes, ``handle_timeout`` is a no-op and returns
    the existing state.
    """
    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    aid = uuid.UUID(approval_id)
    cid = uuid.UUID(chatroom_id) if chatroom_id else None
    async with async_session() as db:
        state = await OrchestrationFacade(db).handle_approval_timeout(aid, chatroom_id=cid)
        await db.commit()
    if state is None:
        logger.bind(approval_id=approval_id).info("approval timeout: approval gone")
        return "noop:gone"
    logger.bind(approval_id=approval_id, state=state.value).info("approval timeout handled")
    return state.value


async def evaluate_silence(ctx: dict[str, Any]) -> str:
    """Periodic sweep: fire silence_minutes wake-ups for room-bound agents.

    Runs every 30 s via Arq cron (G.3 / R15.02). Uses the
    ``ChatroomAgentRepository.list_live_bindings`` method to fetch active
    bindings (M19: SQL extracted into repository). Each firing enqueues a
    ``wakeup_agent`` job.

    After a firing the silence timestamp is reset so the trigger re-arms on
    the next T-minute window instead of re-firing on every 30 s sweep — an
    interim debounce until the Phase H agent runtime owns the timer.
    """
    from contexts.conversation.infrastructure.repositories import (
        ChatroomAgentRepository,
    )
    from contexts.orchestration.application.wakeup_service import WakeupService
    from contexts.orchestration.infrastructure import wakeup_state

    redis = ctx["redis"]
    fired = 0
    checked = 0

    batch_size = 500
    async with async_session() as db:
        svc = WakeupService(db)
        repo = ChatroomAgentRepository(db)
        # Paginate to avoid loading all bindings into memory at once.
        offset = 0
        pairs: list[Any] = []
        while True:
            batch = await repo.list_live_bindings(limit=batch_size, offset=offset)
            pairs.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size

        for agent_id, room_id in pairs:
            checked += 1
            try:
                if await svc.evaluate_silence_trigger(agent_id=agent_id, room_id=room_id):
                    await redis.enqueue_job(
                        "wakeup_agent",
                        str(agent_id),
                        str(room_id),
                        "silence_minutes",
                    )
                    # Debounce: re-arm the timer so the next 30 s sweep does
                    # not immediately re-fire the same silence trigger.
                    await wakeup_state.touch_silence_timestamp(agent_id, room_id)
                    fired += 1
            except Exception:
                # One bad pair must not abort the sweep; clear any aborted
                # transaction so subsequent reads on this session succeed.
                await db.rollback()
                logger.bind(agent_id=str(agent_id), room_id=str(room_id)).exception(
                    "evaluate_silence: trigger check failed"
                )

    logger.bind(event="silence_sweep_done", checked=checked, fired=fired).info(
        f"silence sweep: {fired}/{checked} bound agents woken"
    )
    return f"fired={fired}"


async def wakeup_refresh(ctx: dict[str, Any]) -> str:
    """Periodic: snap wakeup_config back to authored values (G.5 / R15.09).

    Runs hourly. Iterates agents with a non-null ``wakeup_authored_snapshot``
    and calls refresh if the current config has drifted from authored values.
    """
    from contexts.agents.interfaces.facade import AgentsFacade
    from contexts.orchestration.application.wakeup_service import WakeupService

    refreshed = 0
    async with async_session() as db:
        svc = WakeupService(db)
        facade = AgentsFacade(db)
        candidates = await facade.list_agents_with_authored_snapshot()
        for agent in candidates:
            try:
                if await svc.refresh_wakeup_config(agent.id):
                    refreshed += 1
            except Exception:
                logger.bind(agent_id=str(agent.id)).exception("wakeup refresh failed")
        await db.commit()

    logger.bind(event="wakeup_refresh_done", refreshed=refreshed).info(
        f"wakeup refresh sweep: {refreshed} agents refreshed"
    )
    return "ok"


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
    "approval_timeout",
    "evaluate_silence",
    "make_dlq_audit_callback",
    "wakeup_agent",
    "wakeup_refresh",
]
