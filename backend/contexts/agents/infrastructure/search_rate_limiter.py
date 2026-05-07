"""Redis-backed project-scoped rate limiter (R12.14).

Fixed-window counter keyed by the current UTC minute. 60/min default, tunable
per project by the admin surface (not in this file; the caller supplies
``limit_per_minute``).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RedisSearchRateLimiter:
    async def try_acquire(self, *, project_id: uuid.UUID, limit_per_minute: int) -> bool:
        if limit_per_minute <= 0:
            return False
        from shared_kernel.auth.clients import get_redis

        client = get_redis()
        window = int(time.time()) // 60
        key = f"search:rl:{project_id}:{window}"
        pipe = client.pipeline(transaction=False)
        pipe.incr(key, 1)
        pipe.expire(key, 70)
        results = await pipe.execute()
        count = int(results[0])
        return count <= int(limit_per_minute)


__all__ = ["RedisSearchRateLimiter"]
