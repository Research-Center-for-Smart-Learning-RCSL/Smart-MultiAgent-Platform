"""Minute-bucket sliding-window counters (D.8 / R7.10).

Key schema
----------

One hash per (subject, minute):

    keyuse:{subject_id}:{minute_bucket}   (TYPE hash)
        input_tokens   INT
        output_tokens  INT
        requests       INT

`minute_bucket` is `int(unix_time_seconds // 60)`. TTL is 61 minutes so the
tail of the trailing-60 window is always present; a 60-minute TTL would
occasionally race a bucket into expiry just as the aggregator reads it.

Writes
------

Every outbound provider call `record(...)` bumps three counters in one
`HINCRBY`-ish pipeline (three per bucket — `input`, `output`, `requests`).
Writes never block the user response; a Redis outage degrades to "no
limits enforced" which is the correct failure mode for BYO keys (the
provider will reject if real quota is actually exhausted).

Reads
-----

`usage(...)` walks the trailing 60 buckets in one `EVAL`. The Lua aggregator
sums each field across the window — one round-trip for the whole read.
Sub-bucket error per the D.∞ gate is ±1/60 ≈ 1.67 % which sits inside the
±2 % tolerance.

SoC
---

This module is pure Redis. It knows nothing about `contexts.keys`; the keys
context wraps it with its own key-id → Redis-subject mapping so the router
can swap subjects (api_keys, search_keys, future quota sources) without the
bucket lib caring.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Final

from redis.asyncio import Redis

from shared_kernel.auth.clients import get_redis

_BUCKET_SECONDS: Final = 60
_WINDOW_BUCKETS: Final = 60
_BUCKET_TTL_SECONDS: Final = _BUCKET_SECONDS * (_WINDOW_BUCKETS + 1)

# Lua: sum input/output/requests across the trailing `n` minute-buckets.
# KEYS = { bucket0, bucket1, ..., bucketN-1 }   -- newest first
# Returns: { sum_input, sum_output, sum_requests }
_LUA_AGGREGATE: Final = """
local ti, to, tr = 0, 0, 0
for i = 1, #KEYS do
  local v = redis.call('HMGET', KEYS[i], 'input_tokens', 'output_tokens', 'requests')
  if v[1] then ti = ti + tonumber(v[1]) end
  if v[2] then to = to + tonumber(v[2]) end
  if v[3] then tr = tr + tonumber(v[3]) end
end
return {ti, to, tr}
"""


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    requests: int


def _current_bucket(now_s: float | None = None) -> int:
    return int((now_s if now_s is not None else time.time()) // _BUCKET_SECONDS)


def _subject_key(subject_id: uuid.UUID, bucket: int) -> str:
    return f"keyuse:{subject_id}:{bucket}"


async def record(
    subject_id: uuid.UUID,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    requests: int = 0,
    client: Redis | None = None,
) -> None:
    """Increment the current bucket's fields. At least one delta must be > 0."""
    if input_tokens == 0 and output_tokens == 0 and requests == 0:
        return
    redis = client or get_redis()
    key = _subject_key(subject_id, _current_bucket())
    pipe = redis.pipeline(transaction=False)
    if input_tokens:
        pipe.hincrby(key, "input_tokens", input_tokens)
    if output_tokens:
        pipe.hincrby(key, "output_tokens", output_tokens)
    if requests:
        pipe.hincrby(key, "requests", requests)
    pipe.expire(key, _BUCKET_TTL_SECONDS)
    await pipe.execute()


async def usage(
    subject_id: uuid.UUID,
    *,
    client: Redis | None = None,
) -> Usage:
    """Sum the trailing `_WINDOW_BUCKETS` buckets."""
    redis = client or get_redis()
    cur = _current_bucket()
    keys = [_subject_key(subject_id, cur - i) for i in range(_WINDOW_BUCKETS)]
    raw = await redis.eval(_LUA_AGGREGATE, len(keys), *keys)
    return Usage(
        input_tokens=int(raw[0]),
        output_tokens=int(raw[1]),
        requests=int(raw[2]),
    )


__all__ = ["Usage", "record", "usage"]
