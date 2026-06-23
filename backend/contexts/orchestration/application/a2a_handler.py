"""A2A inbox message handler (G.1 + K.3 Pass 2).

Runs in the per-agent A2A consumer loop (one loop per agent, so an inline turn
only serialises *that* agent's inbox). Dispatches one inbound envelope:

- ``reply``    — handed to the reply rendezvous so a waiting ``call`` wakes.
- ``call``     — runs a headless agent turn with the payload as input and
                 delivers the reply (R9.15). Any failure becomes a fail-fast
                 error reply so the synchronous caller never blocks to timeout.
- ``instruct`` — runs a turn and drives the instruction state machine
                 (delivered → completed, or timeout on failure) (R15.15–R15.17).
- ``notify``   — parked in the agent's pending-notification queue and folded
                 into its next turn's context; no immediate turn (R9.16).

A handler that returns normally ACKs the message; only a raised exception is
retried/DLQ'd by the consumer. ``call`` therefore swallows turn failures (it has
already answered the caller); ``instruct``/``notify`` let genuine infra errors
propagate so the consumer can retry.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from contexts.orchestration.domain.models import A2AEnvelope, A2AMessageType
from contexts.orchestration.infrastructure import a2a_rendezvous, pending_notify
from shared_kernel.db.session import async_session

logger = logging.getLogger(__name__)


async def handle_envelope(envelope: A2AEnvelope) -> None:
    """Consumer ``MessageHandler`` — dispatch one inbound A2A envelope."""
    # K.4: fan an a2a signal to workflows — resume parked ``a2a_message`` waits
    # and start dormant ``a2a_event`` triggers for this recipient. Best-effort
    # and out-of-band: a dispatch hiccup must not block or fail inbox handling.
    await _dispatch_a2a_workflow_signal(envelope)

    if envelope.type is A2AMessageType.REPLY:
        await a2a_rendezvous.deliver_reply(envelope.correlation_id, envelope.to_dict())
        return

    if envelope.type is A2AMessageType.CALL:
        await _handle_call(envelope)
        return

    if envelope.type is A2AMessageType.INSTRUCT:
        await _handle_instruct(envelope)
        return

    # NOTIFY — park for the agent's next turn; no immediate turn (R9.16).
    try:
        to_id = uuid.UUID(envelope.to_agent)
    except ValueError:
        logger.warning("a2a notify with non-uuid to_agent %s dropped", envelope.to_agent)
        return
    await pending_notify.push(
        to_id,
        {
            "kind": "notify",
            "from_agent": str(envelope.from_agent) if envelope.from_agent else None,
            "payload": envelope.payload,
        },
    )


# --------------------------------------------------------------------------- #
# CALL
# --------------------------------------------------------------------------- #


async def _handle_call(envelope: A2AEnvelope) -> None:
    try:
        to_id = uuid.UUID(envelope.to_agent)
    except ValueError:
        await _deliver_error(envelope, "call target is not a valid agent id")
        return
    try:
        result = await _run_turn(to_id, envelope)
    except Exception:  # fail-fast: the caller is blocking on the rendezvous
        logger.exception("a2a call %s turn raised", envelope.correlation_id)
        await _deliver_error(envelope, "agent turn failed")
        return

    if result.status != "completed":
        await _deliver_error(envelope, f"agent turn {result.status}: {result.reason or ''}".strip())
        return

    reply_env = {
        "type": A2AMessageType.REPLY.value,
        "from_agent": envelope.to_agent,
        "to_agent": str(envelope.from_agent) if envelope.from_agent else None,
        "correlation_id": str(envelope.correlation_id),
        # Top-level for the workflow agent_invocation executor; also in payload
        # for the standard reply convention.
        "reply": result.text,
        "payload": {"reply": result.text, "output": result.text},
    }
    await a2a_rendezvous.deliver_reply(envelope.correlation_id, reply_env)


# --------------------------------------------------------------------------- #
# INSTRUCT
# --------------------------------------------------------------------------- #


async def _handle_instruct(envelope: A2AEnvelope) -> None:
    try:
        to_id = uuid.UUID(envelope.to_agent)
    except ValueError:
        logger.warning("a2a instruct with non-uuid to_agent %s dropped", envelope.to_agent)
        return
    instruction_id = _maybe_uuid(envelope.payload.get("instruction_id"))

    from contexts.orchestration.interfaces.facade import OrchestrationFacade

    async with async_session() as db:
        if instruction_id is not None:
            await OrchestrationFacade(db).mark_instruct_delivered(instruction_id)
            await db.commit()
        result = await _run_turn_with_db(db, to_id, envelope)
        if instruction_id is not None:
            facade = OrchestrationFacade(db)
            if result.status == "completed":
                await facade.mark_instruct_completed(instruction_id)
            else:
                # Provider/turn failure — not a deadline timeout. mark_failed
                # records the real cause (instruct.failed audit) while keeping
                # a settled state the workflow resume task maps to ``failure``.
                from contexts.orchestration.application.instruct_service import InstructService

                await InstructService(db).mark_failed(instruction_id)
            await db.commit()

    # K.4: resume any workflow run parked on this instruct node — post-commit so
    # the resume task reads the settled (completed/timeout) state. No-ops for a
    # non-workflow instruct (no ``wf:instruct:{id}`` claim key).
    if instruction_id is not None:
        from shared_kernel.queue import enqueue

        try:
            await enqueue("workflow_resume_instruct", str(instruction_id))
        except Exception:
            logger.warning("instruct %s: workflow resume dispatch failed", instruction_id, exc_info=True)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _run_turn(to_id: uuid.UUID, envelope: A2AEnvelope) -> Any:
    async with async_session() as db:
        return await _run_turn_with_db(db, to_id, envelope)


async def _run_turn_with_db(db: Any, to_id: uuid.UUID, envelope: A2AEnvelope) -> Any:
    from app.config.settings import get_settings
    from contexts.agents.application.runtime.turn_engine import TurnEngine

    settings = get_settings()
    engine = TurnEngine(
        db,
        qdrant_url=settings.qdrant.url,
        qdrant_api_key=settings.qdrant.api_key,
    )
    return await engine.run_input_turn(
        agent_id=to_id,
        input_text=_input_from_payload(envelope.payload),
        # Attribute the turn's usage to the calling agent (guarded: a non-UUID
        # sender simply yields no attribution rather than failing the turn).
        parent_agent_id=_maybe_uuid(envelope.from_agent),
        workflow_run_id=envelope.workflow_run_id,
    )


def _input_from_payload(payload: dict[str, Any]) -> str:
    for key in ("input", "message", "text", "content"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    import json

    return json.dumps(payload, separators=(",", ":"))


def _maybe_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def _dispatch_a2a_workflow_signal(envelope: A2AEnvelope) -> None:
    from shared_kernel.queue import enqueue

    try:
        await enqueue(
            "workflow_signal",
            "a2a",
            {"target_agent_id": str(envelope.to_agent), "msg_type": envelope.type.value},
        )
    except Exception:
        logger.warning("a2a workflow-signal dispatch failed for %s", envelope.to_agent, exc_info=True)


async def _deliver_error(envelope: A2AEnvelope, detail: str) -> None:
    """Best-effort error reply — must never raise so callers can return
    normally and ACK the envelope (preventing infinite consumer retries)."""
    try:
        error_reply = {
            "type": A2AMessageType.REPLY.value,
            "from_agent": envelope.to_agent,
            "to_agent": str(envelope.from_agent) if envelope.from_agent else None,
            "correlation_id": str(envelope.correlation_id),
            "payload": {
                a2a_rendezvous.A2A_ERROR_KEY: "agent call failed",
                "detail": detail,
            },
        }
        await a2a_rendezvous.deliver_reply(envelope.correlation_id, error_reply)
        logger.info("a2a call %s answered with error reply: %s", envelope.correlation_id, detail)
    except Exception:
        logger.exception("a2a call %s: error delivery failed (detail: %s)", envelope.correlation_id, detail)


__all__ = ["handle_envelope"]
