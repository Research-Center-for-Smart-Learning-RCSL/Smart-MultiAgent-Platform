"""Pure functions for retry/backoff + rotation classification (D.7)."""

from __future__ import annotations

import enum
import random
from dataclasses import dataclass

from contexts.keys.domain.groups import RotationPolicy


class RotationReason(str, enum.Enum):
    OK = "ok"
    ROTATE = "rotate"  # recoverable error on this member → try next
    RETRY = "retry"  # same member, backoff then retry
    QUOTA = "token_quota"  # bucket cap hit; treat as temporarily unavailable
    FATAL = "fatal"  # bad secret on THIS member → skip it, but a sibling key
    # (different secret) may still work, so the group keeps rotating.
    ABORT = "abort"  # the request itself is rejected (bad/invalid body, unknown
    # model) → identical on every key, so abort the whole group, don't rotate.


# Deterministic request-level rejections: the provider faulted the *request*,
# not the key. Rotating to another key replays the same bad request and only
# burns the rest of the group (cf. the agent adapters' empty-message guards).
_ABORT_STATUSES = frozenset({400, 404, 422})


@dataclass(frozen=True, slots=True)
class ErrorOutcome:
    reason: RotationReason
    http_status: int | None
    error_code: str | None


def classify_http(status: int, policy: RotationPolicy) -> ErrorOutcome:
    """Decide what to do with a `status`-returning provider call.

    - 2xx → OK.
    - Listed in `rotate_on_error_codes` → retry (if `retry_on_error`) else
      rotate. An explicit policy entry wins over the defaults below.
    - 401/403 → FATAL (bad secret on this key; a sibling key may still work).
    - 400/404/422 → ABORT (the request is malformed/invalid; every key 400s the
      same, so rotating only burns the group).
    - Everything else → rotate (the provider returned something we don't
      understand; moving on is safer than hammering).
    """
    if 200 <= status < 300:
        return ErrorOutcome(RotationReason.OK, status, None)
    if status in (401, 403):
        return ErrorOutcome(RotationReason.FATAL, status, "provider_unauthorized")
    if status in policy.rotate_on_error_codes:
        reason = RotationReason.RETRY if policy.retry_on_error else RotationReason.ROTATE
        return ErrorOutcome(reason, status, f"http_{status}")
    if status in _ABORT_STATUSES:
        return ErrorOutcome(RotationReason.ABORT, status, f"http_{status}")
    return ErrorOutcome(RotationReason.ROTATE, status, f"http_{status}")


def backoff_delay_ms(
    attempt: int,
    policy: RotationPolicy,
    *,
    rng: random.Random | None = None,
) -> int:
    """Exp backoff with jitter. `attempt` is 1-indexed."""
    r = rng or random
    base = policy.retry_initial_delay_ms * float(policy.retry_multiplier) ** max(0, attempt - 1)
    capped = min(base, float(policy.retry_max_delay_ms))
    jitter_fraction = policy.retry_jitter_pct / 100.0
    jitter = capped * jitter_fraction * (r.random() * 2 - 1)  # symmetric ±
    return max(0, int(capped + jitter))


__all__ = ["ErrorOutcome", "RotationReason", "backoff_delay_ms", "classify_http"]
