"""Arq tasks for the orchestration context (G.3 / G.5 / G.9).

Tasks:
- ``wakeup_agent``          — enqueue an agent wake-up for a specific room
- ``evaluate_silence``      — periodic check of silence triggers across rooms
- ``wakeup_refresh``        — periodic snap of wakeup_config to authored values

G.9 audit wiring: ``make_dlq_audit_callback`` is used by the consumer loop
to emit ``a2a.dlq`` audit events whenever a message is moved to the DLQ.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from shared_kernel.db.session import async_session

logger = logging.getLogger(__name__)


async def wakeup_agent(
    ctx: dict[str, Any], agent_id: str, room_id: str, trigger: str = "every_n_messages",
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

    logger.info("wakeup fired: agent=%s room=%s trigger=%s", agent_id, room_id, trigger)
    return "ok"


async def evaluate_silence(ctx: dict[str, Any]) -> str:
    """Periodic sweep: check silence_minutes triggers for all active agents.

    Runs every 30 seconds via Arq cron. For each agent+room pair where
    the silence trigger fires, enqueues a ``wakeup_agent`` job.
    """
    from arq import ArqRedis

    from contexts.agents.interfaces.facade import AgentsFacade
    from contexts.orchestration.application.wakeup_service import WakeupService
    from contexts.orchestration.domain.models import WakeupConfig
    from shared_kernel import audit
    from shared_kernel.auth.clients import get_redis

    # In production, this would iterate over rooms with bound agents.
    # For now, the task structure is in place; the room-agent binding
    # query will be added when the workspace_agents join table lands (Phase H).
    logger.debug("silence trigger sweep: no-op until room-agent bindings exist")
    return "ok"


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
                logger.exception("wakeup refresh failed for agent %s", agent.id)
        await db.commit()

    logger.info("wakeup refresh sweep: %d agents refreshed", refreshed)
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
            logger.exception("a2a.dlq audit failed for agent %s", agent_id)

    return _on_dlq


__all__ = ["evaluate_silence", "make_dlq_audit_callback", "wakeup_agent", "wakeup_refresh"]
