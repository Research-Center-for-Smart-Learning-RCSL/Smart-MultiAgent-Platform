"""Per-agent pending A2A notification queue (K.3 — R9.16).

An A2A ``notify`` (and a system-originated approval-request notification) does
not trigger an immediate turn; instead it is parked here and folded into the
context of the agent's *next* turn (room or headless). The turn engine drains
this queue at turn start.

Keys:
  a2a:pending_notify:{agent_id}  — Redis list of JSON notification payloads.

SoC: pure Redis state; no domain logic, no DB access.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Final

from shared_kernel.auth.clients import get_redis

# Bound the queue so a notify storm cannot grow an agent's context without
# limit; oldest entries past the cap are trimmed on push.
_MAX_PENDING: Final = 50
_TTL_SECONDS: Final = 86400  # a notification older than a day is stale


def _key(agent_id: uuid.UUID) -> str:
    return f"a2a:pending_notify:{agent_id}"


async def push(agent_id: uuid.UUID, payload: dict[str, Any]) -> None:
    """Append a notification for ``agent_id`` (newest last), trimmed to cap."""
    r = get_redis()
    key = _key(agent_id)
    pipe = r.pipeline(transaction=False)
    pipe.rpush(key, json.dumps(payload, separators=(",", ":")))
    pipe.ltrim(key, -_MAX_PENDING, -1)
    pipe.expire(key, _TTL_SECONDS)
    await pipe.execute()


async def drain(agent_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return and clear all pending notifications for ``agent_id``.

    Read-then-delete is pipelined (not atomic): a notify arriving between the
    LRANGE and the DELETE is rare and at worst dropped — acceptable for
    best-effort context injection.
    """
    r = get_redis()
    key = _key(agent_id)
    pipe = r.pipeline(transaction=False)
    pipe.lrange(key, 0, -1)
    pipe.delete(key)
    raw, _ = await pipe.execute()
    out: list[dict[str, Any]] = []
    for item in raw or []:
        try:
            parsed = json.loads(item)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


__all__ = ["drain", "push"]
