"""Usage-event emitter (D.9).

One function. Every provider call the router makes ends with a `record(...)`
— both successes and errors. Errors carry `error_code`; `http_status` is
NULL on transport failures (connection refused etc.) so the reader can
distinguish them.

Best-effort: a Redis/DB hiccup must NOT fail the user response. The router
catches and logs, never re-raises.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.infrastructure import tables as t

_log = logging.getLogger(__name__)


async def record_usage_event(
    db: AsyncSession,
    *,
    key_id: uuid.UUID,
    input_tokens: int,
    output_tokens: int,
    request_ms: int,
    http_status: int | None,
    error_code: str | None,
    agent_id: uuid.UUID | None = None,
    parent_agent_id: uuid.UUID | None = None,
    chatroom_id: uuid.UUID | None = None,
) -> None:
    try:
        await db.execute(
            t.key_usage_events.insert().values(
                key_id=key_id,
                agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                chatroom_id=chatroom_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_ms=request_ms,
                http_status=http_status,
                error_code=error_code,
            )
        )
    except Exception:
        # Non-blocking by contract. Swallow + log so router retries / returns
        # the provider response rather than surfacing "failed to account".
        _log.exception("record_usage_event failed key_id=%s", key_id)


__all__ = ["record_usage_event"]
