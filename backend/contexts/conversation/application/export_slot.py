"""One active export per owner and scope, held as a Redis key naming the job.

Shared by the chat export (per room) and the research export (per workspace).
The caller decides whether the job a key names still blocks a new one, so each
export keeps its own staleness rule and its own record format.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Final

# Replace the slot's holder only if it is still the one that was inspected, so
# two creates taking over a finished holder at once cannot both win.
_TAKEOVER: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""


async def claim(
    redis: Any,
    key: str,
    job_id: uuid.UUID,
    *,
    ttl_seconds: int,
    blocks: Callable[[uuid.UUID], Awaitable[bool]],
) -> uuid.UUID | None:
    """Point ``key`` at ``job_id``; return the blocking holder's id instead if one blocks.

    ``blocks(holder)`` says whether the job the key names still holds the slot.
    It may record a side effect, such as marking an abandoned holder failed,
    before answering ``False``.
    """
    for _ in range(3):
        if await redis.set(key, str(job_id), ex=ttl_seconds, nx=True):
            return None
        held = await redis.get(key)
        if held is None:
            continue  # released between the two calls; claim it afresh
        holder = uuid.UUID(held)
        if await blocks(holder):
            return holder
        if await redis.eval(_TAKEOVER, 1, key, held, str(job_id), str(ttl_seconds)):
            return None
    # Lost every race for the slot: another create just won it.
    winner = await redis.get(key)
    return uuid.UUID(winner) if winner else job_id


__all__ = ["claim"]
