"""A2A reply rendezvous (G.1 — synchronous-call handshake).

The background A2A consumer (``app.workers``) is the *sole* reader of every
agent inbox stream. A synchronous ``call`` therefore cannot read its own reply
off the stream itself — it would race the consumer for the same consumer-group
entry. Instead the consumer's handler hands every ``reply`` envelope to this
rendezvous, and ``call`` blocks on a short-lived Redis list keyed by the
call's ``correlation_id``.

A list (``RPUSH`` / ``BLPOP``) is used rather than pub/sub so a reply that
arrives *before* the caller starts waiting is not lost: it sits in the list
until ``BLPOP`` drains it or the key's TTL expires.

SoC: pure Redis state; no domain logic, no DB access.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable
from typing import Any, Final, cast

from shared_kernel.auth.clients import get_redis

# Reply payload marker: set by the degraded inbox handler when it answers a
# CALL it cannot actually serve, so a waiting ``call`` can tell a fail-fast
# error reply apart from a genuine agent reply.
A2A_ERROR_KEY: Final = "__a2a_error__"

# The rendezvous list outlives a slow caller but must not leak forever.
_REPLY_TTL_SECONDS: Final = 900


def _reply_key(correlation_id: uuid.UUID) -> str:
    return f"a2a:reply:{correlation_id}"


async def deliver_reply(correlation_id: uuid.UUID, envelope: dict[str, Any]) -> None:
    """Hand a reply to whichever ``call`` is waiting on this correlation id.

    The push and its TTL are pipelined so the rendezvous list can never be
    left without an expiry if the process dies mid-call.
    """
    key = _reply_key(correlation_id)
    pipe = get_redis().pipeline(transaction=False)
    pipe.rpush(key, json.dumps(envelope, separators=(",", ":")))
    pipe.expire(key, _REPLY_TTL_SECONDS)
    await pipe.execute()


async def await_reply(
    correlation_id: uuid.UUID,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """Block until a reply for ``correlation_id`` arrives or the timeout lapses.

    Returns the reply envelope dict, or ``None`` on timeout.
    """
    key = _reply_key(correlation_id)
    # BLPOP takes an integer-second timeout; 0 means "block forever", so clamp
    # to >= 1 s — a sub-second or zero deadline must still return promptly.
    blpop_timeout = max(1, int(timeout_seconds))
    # redis-py types blocking list ops as the sync/async ResponseT union;
    # cast to the async branch so the await type-checks cleanly.
    result: Any = await cast(
        Awaitable[Any],
        get_redis().blpop([key], timeout=blpop_timeout),
    )
    if result is None:
        return None
    _key, raw = result
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ["A2A_ERROR_KEY", "await_reply", "deliver_reply"]
