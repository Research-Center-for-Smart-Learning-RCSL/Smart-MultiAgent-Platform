"""Arq tasks for the orchestration context (G.3 / G.5 / G.9).

Tasks:
- ``wakeup_agent``          — enqueue an agent wake-up for a specific room
- ``evaluate_silence``      — periodic check of silence triggers across rooms
- ``wakeup_refresh``        — periodic snap of wakeup_config to authored values

G.9 audit wiring: ``make_dlq_audit_callback`` is used by the consumer loop
to emit ``a2a.dlq`` audit events whenever a message is moved to the DLQ.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from shared_kernel.db.session import async_session


async def wakeup_agent(
    ctx: dict[str, Any],
    agent_id: str,
    room_id: str,
    trigger: str = "every_n_messages",
) -> str:
    """Fire a wake-up for a single agent in a single room.

    Enqueued by the wake-up evaluator when a trigger fires.
    The actual LLM turn execution is orchestrated by the agent runtime
    (Phase H); this task just marks the wake-up as fired and logs the
    audit event.
    """
    from shared_kernel import audit

    aid = uuid.UUID(agent_id)
    rid = uuid.UUID(room_id)

    async with async_session() as db:
        await audit.emit(
            db,
            audit.AuditEvent(
                action="wakeup.fired",
                resource_type="agent",
                resource_id=aid,
                metadata={"room_id": str(rid), "trigger": trigger},
            ),
        )
        await db.commit()

    logger.bind(
        event="wakeup_fired",
        agent_id=agent_id,
        room_id=room_id,
        trigger=trigger,
    ).info("wakeup fired")
    return "ok"


async def evaluate_silence(ctx: dict[str, Any]) -> str:
    """Periodic sweep: fire silence_minutes wake-ups for room-bound agents.

    Runs every 30 s via Arq cron (G.3 / R15.02). Iterates every
    ``chatroom_agents`` binding in a live (non-deleted) chatroom and asks
    ``WakeupService.evaluate_silence_trigger`` whether the agent has been
    silent long enough to wake. Each firing enqueues a ``wakeup_agent`` job.

    After a firing the silence timestamp is reset so the trigger re-arms on
    the next T-minute window instead of re-firing on every 30 s sweep — an
    interim debounce until the Phase H agent runtime owns the timer.
    """
    import sqlalchemy as sa

    from contexts.conversation.infrastructure.tables import chatroom_agents, chatrooms
    from contexts.orchestration.application.wakeup_service import WakeupService
    from contexts.orchestration.infrastructure import wakeup_state

    redis = ctx["redis"]
    fired = 0
    checked = 0

    async with async_session() as db:
        svc = WakeupService(db)
        pairs = (
            await db.execute(
                sa.select(chatroom_agents.c.agent_id, chatroom_agents.c.chatroom_id)
                .select_from(
                    chatroom_agents.join(
                        chatrooms,
                        chatrooms.c.id == chatroom_agents.c.chatroom_id,
                    )
                )
                .where(chatrooms.c.deleted_at.is_(None))
            )
        ).all()

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


__all__ = ["evaluate_silence", "make_dlq_audit_callback", "wakeup_agent", "wakeup_refresh"]
