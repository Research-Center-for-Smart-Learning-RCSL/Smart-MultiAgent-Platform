"""A2A inbox consumer (G.1).

One consumer loop per live agent runtime. Reads from the agent's inbox
stream, dispatches to the agent's turn handler, ACKs on success, retries
with exponential backoff (max 3), pushes to DLQ on final failure.

Retry counting uses Redis Stream's built-in delivery count (reported by
XPENDING) rather than a custom field — this survives consumer crashes.

SoC:
- Stream I/O → ``infrastructure.a2a_streams``
- Message dispatch → caller-provided ``handler`` callback
- Metrics → ``infrastructure.metrics``

This module is invoked by the Arq worker; it does NOT import FastAPI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final

from contexts.orchestration.domain.models import A2AEnvelope
from contexts.orchestration.infrastructure import a2a_streams
from contexts.orchestration.infrastructure.metrics import A2A_DLQ

logger = logging.getLogger(__name__)

_MAX_RETRIES: Final = 3
_BACKOFF_BASE: Final = 1.0

MessageHandler = Callable[[A2AEnvelope], Awaitable[None]]
# Called when a message is moved to DLQ: (agent_id, envelope_json, error, attempt)
DlqCallback = Callable[[uuid.UUID, str, str, int], Awaitable[None]]


async def consume_once(
    agent_id: uuid.UUID,
    handler: MessageHandler,
    *,
    on_dlq: DlqCallback | None = None,
) -> int:
    """Process one batch from the agent's inbox.

    Returns the number of messages successfully processed.
    Called in a loop by the agent runtime or Arq task.

    ``on_dlq`` is called (awaited) after a message is moved to DLQ so callers
    can emit the ``a2a.dlq`` audit event with a DB session.
    """
    processed = 0

    # 1. Drain pending (previously read but un-ACKed) first.
    # Use delivery counts from XPENDING to track retry attempts.
    delivery_counts = await a2a_streams.get_pending_delivery_counts(agent_id)
    pending = await a2a_streams.xread_pending(agent_id, count=10)
    for stream_id, fields in pending:
        attempt = delivery_counts.get(stream_id, 1)
        processed += await _process_entry(
            agent_id, stream_id, fields, handler, attempt, on_dlq=on_dlq,
        )

    # 2. Read new messages (blocks up to 1s).
    new = await a2a_streams.xread_new(agent_id, count=10)
    for stream_id, fields in new:
        processed += await _process_entry(
            agent_id, stream_id, fields, handler, 1, on_dlq=on_dlq,
        )

    return processed


async def run_consumer_loop(
    agent_id: uuid.UUID,
    handler: MessageHandler,
    *,
    shutdown_event: asyncio.Event | None = None,
    on_dlq: DlqCallback | None = None,
) -> None:
    """Long-running consumer loop for an agent's A2A inbox.

    Runs until ``shutdown_event`` is set or the task is cancelled.
    Pass ``on_dlq`` to receive audit callbacks when messages enter the DLQ.
    """
    await a2a_streams.ensure_consumer_group(agent_id)

    while True:
        if shutdown_event and shutdown_event.is_set():
            break
        try:
            await consume_once(agent_id, handler, on_dlq=on_dlq)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("a2a consumer error for agent %s", agent_id)
            await asyncio.sleep(_BACKOFF_BASE)


async def _process_entry(
    agent_id: uuid.UUID,
    stream_id: str,
    fields: dict[str, str],
    handler: MessageHandler,
    attempt: int,
    *,
    on_dlq: DlqCallback | None = None,
) -> int:
    """Try to process a single stream entry. Returns 1 on success, 0 on DLQ."""
    raw = fields.get("envelope", "{}")

    try:
        data = json.loads(raw)
        envelope = A2AEnvelope.from_dict(data)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.warning(
            "unparseable a2a message for agent %s (stream_id=%s): %s",
            agent_id, stream_id, exc,
        )
        error_msg = f"parse error: {exc}"
        await a2a_streams.move_to_dlq(agent_id, stream_id, raw, error_msg, attempt)
        A2A_DLQ.inc()
        if on_dlq:
            await on_dlq(agent_id, raw, error_msg, attempt)
        return 0

    try:
        await handler(envelope)
        await a2a_streams.xack(agent_id, stream_id)
        return 1
    except Exception as exc:
        logger.warning(
            "handler error for agent %s (stream_id=%s, attempt=%d): %s",
            agent_id, stream_id, attempt, exc,
        )
        if attempt >= _MAX_RETRIES:
            error_msg = str(exc)
            await a2a_streams.move_to_dlq(agent_id, stream_id, raw, error_msg, attempt)
            A2A_DLQ.inc()
            if on_dlq:
                await on_dlq(agent_id, raw, error_msg, attempt)
            logger.error(
                "a2a message moved to DLQ for agent %s after %d attempts",
                agent_id, _MAX_RETRIES,
            )
            return 0

        # Leave un-ACKed — next consume_once will see it as pending with
        # an incremented delivery count from the stream's consumer group.
        return 0


__all__ = ["DlqCallback", "MessageHandler", "consume_once", "run_consumer_loop"]
