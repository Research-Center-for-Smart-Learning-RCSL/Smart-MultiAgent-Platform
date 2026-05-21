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
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Final

from contexts.orchestration.domain.models import A2AEnvelope
from contexts.orchestration.infrastructure import a2a_streams
from contexts.orchestration.infrastructure.metrics import A2A_DLQ
from shared_kernel.auth.clients import get_redis

logger = logging.getLogger(__name__)

_MAX_RETRIES: Final = 3
_BACKOFF_BASE: Final = 1.0
# ASYNC-8: a failing message keeps a Redis retry record (attempt count + the
# earliest-retry timestamp). The TTL is far longer than any realistic backoff
# chain (~7 s for 3 attempts) and is refreshed on every failure, so it only
# ever reaps orphaned records — never one mid-retry.
_RETRY_KEY_TTL: Final = 3600
# How often run_consumer_loop reclaims inbox entries stranded by a dead or
# stalled consumer via XAUTOCLAIM (see a2a_streams.xautoclaim_stale).
_CLAIM_INTERVAL_SECONDS: Final = 30.0

MessageHandler = Callable[[A2AEnvelope], Awaitable[None]]
# Called when a message is moved to DLQ: (agent_id, envelope_json, error, attempt)
DlqCallback = Callable[[uuid.UUID, str, str, int], Awaitable[None]]


def _retry_key(agent_id: uuid.UUID, stream_id: str) -> str:
    """Redis key holding a failing message's retry record (ASYNC-8)."""
    return f"a2a:retry:{agent_id}:{stream_id}"


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
    redis = get_redis()
    now_ms = int(time.time() * 1000)

    # 1. Drain pending (previously read but un-ACKed) entries.
    # ASYNC-8: each failing message carries a Redis retry record. An entry still
    # inside its exponential, jittered backoff window is skipped this round
    # instead of being re-delivered on every loop spin (which would hammer a
    # deterministically-failing dependency until the 3 attempts exhaust).
    pending = await a2a_streams.xread_pending(agent_id, count=10)
    for stream_id, fields in pending:
        state = await redis.hgetall(_retry_key(agent_id, stream_id))
        if state:
            if int(state.get("next_at_ms", 0)) > now_ms:
                # Still backing off — leave it pending, re-check next round.
                continue
            attempt = int(state.get("attempt", 0)) + 1
        else:
            # No record (first retry, or the record expired) — treat as attempt 1.
            attempt = 1
        processed += await _process_entry(
            agent_id,
            stream_id,
            fields,
            handler,
            attempt,
            on_dlq=on_dlq,
        )

    # 2. Read new messages (blocks up to 1s).
    new = await a2a_streams.xread_new(agent_id, count=10)
    for stream_id, fields in new:
        processed += await _process_entry(
            agent_id,
            stream_id,
            fields,
            handler,
            1,
            on_dlq=on_dlq,
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

    Every ``_CLAIM_INTERVAL_SECONDS`` the loop reclaims inbox entries stranded
    by a crashed or stalled consumer (``XAUTOCLAIM``), so a worker that dies
    mid-processing — or a peer replica — does not leave A2A messages pending
    forever. Reclaimed entries land in this consumer's PEL and are drained by
    the next :func:`consume_once`.
    """
    await a2a_streams.ensure_consumer_group(agent_id)

    last_claim = 0.0
    while True:
        if shutdown_event and shutdown_event.is_set():
            break
        try:
            now = time.monotonic()
            if now - last_claim >= _CLAIM_INTERVAL_SECONDS:
                # Advance the clock *before* attempting so a reclaim failure
                # (e.g. a Redis older than 6.2, which has no XAUTOCLAIM) backs
                # off to the next interval. The reclaim is best-effort: its
                # failure is caught here so it can never skip consume_once —
                # the periodic reclaim degrades, normal consumption does not.
                last_claim = now
                try:
                    reclaimed = await a2a_streams.xautoclaim_stale(agent_id)
                except Exception:
                    logger.exception(
                        "a2a consumer: stale-message reclaim failed for agent %s",
                        agent_id,
                    )
                else:
                    if reclaimed:
                        logger.info(
                            "a2a consumer reclaimed %d stranded message(s) for agent %s",
                            reclaimed,
                            agent_id,
                        )
            await consume_once(agent_id, handler, on_dlq=on_dlq)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("a2a consumer error for agent %s", agent_id)
            await asyncio.sleep(_BACKOFF_BASE)


class A2AConsumerSupervisor:
    """Discovers agent inbox streams and runs one consumer loop per agent.

    A single long-lived task owned by ``app.workers``. Every
    ``_SCAN_INTERVAL_SECONDS`` it SCANs Redis for ``a2a:agent:{id}`` streams
    and ensures a :func:`run_consumer_loop` task is running for each. Agents
    created after start-up are picked up on the next scan; loops that have
    exited are restarted. :meth:`stop` cancels every child loop cleanly.

    This is the *sole* reader of agent inbox streams — synchronous ``call``
    waits on the reply rendezvous (``a2a_rendezvous``) rather than the stream,
    so there is no consumer-group race between the two.
    """

    _SCAN_INTERVAL_SECONDS: Final = 10.0
    _INBOX_MATCH: Final = "a2a:agent:*"
    _INBOX_PREFIX: Final = "a2a:agent:"

    def __init__(
        self,
        handler: MessageHandler,
        *,
        on_dlq: DlqCallback | None = None,
    ) -> None:
        self._handler = handler
        self._on_dlq = on_dlq
        self._shutdown = asyncio.Event()
        self._loops: dict[uuid.UUID, asyncio.Task[None]] = {}

    async def run(self) -> None:
        """Long-lived supervisor loop — runs until cancelled or stopped."""
        logger.info("a2a consumer supervisor started")
        try:
            while not self._shutdown.is_set():
                try:
                    await self._reconcile()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("a2a consumer supervisor: reconcile failed")
                # Sleep until the next scan, but wake immediately on shutdown.
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=self._SCAN_INTERVAL_SECONDS,
                    )
        except asyncio.CancelledError:
            pass
        finally:
            await self._stop_all()
            logger.info("a2a consumer supervisor stopped")

    async def stop(self) -> None:
        """Signal the supervisor and every child loop to wind down."""
        self._shutdown.set()

    async def _reconcile(self) -> None:
        for agent_id in await self._discover_agents():
            task = self._loops.get(agent_id)
            if task is None or task.done():
                self._loops[agent_id] = asyncio.create_task(
                    run_consumer_loop(
                        agent_id,
                        self._handler,
                        shutdown_event=self._shutdown,
                        on_dlq=self._on_dlq,
                    ),
                    name=f"a2a-consumer-{agent_id}",
                )

    async def _discover_agents(self) -> set[uuid.UUID]:
        found: set[uuid.UUID] = set()
        async for key in get_redis().scan_iter(match=self._INBOX_MATCH, count=200):
            # decode_responses=True → key is str. Skip the ":dlq" sibling streams.
            if key.endswith(":dlq"):
                continue
            try:
                found.add(uuid.UUID(key.removeprefix(self._INBOX_PREFIX)))
            except ValueError:
                continue
        return found

    async def _stop_all(self) -> None:
        self._shutdown.set()
        pending = [t for t in self._loops.values() if not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        self._loops.clear()


async def _process_entry(
    agent_id: uuid.UUID,
    stream_id: str,
    fields: dict[str, str],
    handler: MessageHandler,
    attempt: int,
    *,
    on_dlq: DlqCallback | None = None,
) -> int:
    """Try to process a single stream entry. Returns 1 on success, 0 otherwise.

    ``attempt`` is the 1-based number of this handler invocation. On a
    non-final failure the entry is left un-ACKed and a Redis retry record is
    written so the next ``consume_once`` defers re-delivery by an exponential,
    jittered backoff (ASYNC-8). On success or DLQ the record is cleared.
    """
    redis = get_redis()
    retry_key = _retry_key(agent_id, stream_id)
    raw = fields.get("envelope", "{}")

    try:
        data = json.loads(raw)
        envelope = A2AEnvelope.from_dict(data)
    except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        logger.warning(
            "unparseable a2a message for agent %s (stream_id=%s): %s",
            agent_id,
            stream_id,
            exc,
        )
        error_msg = f"parse error: {exc}"
        await a2a_streams.move_to_dlq(agent_id, stream_id, raw, error_msg, attempt)
        await redis.delete(retry_key)
        A2A_DLQ.inc()
        if on_dlq:
            await on_dlq(agent_id, raw, error_msg, attempt)
        return 0

    try:
        await handler(envelope)
        await a2a_streams.xack(agent_id, stream_id)
        await redis.delete(retry_key)
        return 1
    except Exception as exc:
        logger.warning(
            "handler error for agent %s (stream_id=%s, attempt=%d): %s",
            agent_id,
            stream_id,
            attempt,
            exc,
        )
        if attempt >= _MAX_RETRIES:
            error_msg = str(exc)
            await a2a_streams.move_to_dlq(agent_id, stream_id, raw, error_msg, attempt)
            await redis.delete(retry_key)
            A2A_DLQ.inc()
            if on_dlq:
                await on_dlq(agent_id, raw, error_msg, attempt)
            logger.error(
                "a2a message moved to DLQ for agent %s after %d attempts",
                agent_id,
                _MAX_RETRIES,
            )
            return 0

        # ASYNC-8: non-final failure. Leave the entry un-ACKed (it stays in the
        # consumer group's PEL) and record an exponential, jittered backoff so
        # the next consume_once round defers re-delivery instead of retrying in
        # a tight loop.
        jitter = random.uniform(0.5, 1.5)  # noqa: S311 — retry jitter, not security-sensitive
        backoff_s = _BACKOFF_BASE * (2 ** (attempt - 1)) * jitter
        next_at_ms = int(time.time() * 1000) + int(backoff_s * 1000)
        await redis.hset(
            retry_key,
            mapping={"attempt": attempt, "next_at_ms": next_at_ms},
        )
        await redis.expire(retry_key, _RETRY_KEY_TTL)
        return 0


__all__ = [
    "A2AConsumerSupervisor",
    "DlqCallback",
    "MessageHandler",
    "consume_once",
    "run_consumer_loop",
]
