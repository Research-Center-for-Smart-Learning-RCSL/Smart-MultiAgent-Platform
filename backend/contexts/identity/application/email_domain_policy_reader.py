"""Resolve the effective email-domain policy for one request (R19a.13).

Three stores are in play and only one of them is ever the authority:

* PostgreSQL holds the versioned singleton and the rollout state;
* a versioned JSON key in Redis (`config:email_domain:policy:v2`) accelerates
  reads and is disposable — losing it costs a database read, never correctness;
* the three legacy Redis keys are a rollout/rollback bridge that still governs
  while replicas which know only them may be serving.

**One resolution answers both questions.** The rollout state is a field of the
same cached value as the policy, so a reader learns which authority applies and
what the policy is from one read. Re-reading the phase per request would defeat
the cache; caching the phase without the policy would enforce a stale authority.

**Nothing here may degrade to "no restriction".** A missing, expired, evicted,
malformed or unreadable cache falls back to the database; an unreachable database
raises :class:`EmailDomainPolicyUnavailable`. Reading an unavailable authority as
`off` is precisely how the Redis-only predecessor silently reopened registration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from loguru import logger

from contexts.identity.application.ports import (
    EmailDomainPolicyMirror,
    EmailDomainPolicyRepository,
    LegacyEmailDomainPolicyStore,
    MirroredPolicy,
)
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyRolloutState,
)
from contexts.identity.domain.errors import EmailDomainPolicyUnavailable

#: Matches the mirror's TTL, so a process cache filled from the database expires
#: no later than the mirror it wrote. A cache filled from the mirror is capped to
#: the mirror's *remaining* PTTL instead (AC-12).
LOCAL_CACHE_TTL_SECONDS: Final = 30.0


@dataclass(slots=True)
class _Entry:
    policy: EmailDomainPolicy
    expires_at: float


#: Process-wide, because the acceleration is per process and a per-request cache
#: would accelerate nothing. Reset by :func:`reset_process_cache` in tests.
_entry: _Entry | None = None


def reset_process_cache() -> None:
    """Drop the in-process snapshot. For tests and for a rollout transition
    running inside this process."""
    global _entry
    _entry = None


class EmailDomainPolicyReader:
    """Resolves the phase, then applies that phase's authority."""

    def __init__(
        self,
        *,
        repository: EmailDomainPolicyRepository,
        mirror: EmailDomainPolicyMirror,
        legacy: LegacyEmailDomainPolicyStore,
    ) -> None:
        self._repository = repository
        self._mirror = mirror
        self._legacy = legacy

    async def is_allowed(self, email: str) -> bool:
        """Whether the effective policy admits ``email``'s domain."""
        return (await self.effective_policy()).admits(email)

    async def effective_policy(self) -> EmailDomainPolicy:
        """The policy that governs right now, per the resolved rollout phase."""
        resolved = await self._resolve_state_carrier()
        state = resolved.rollout_state
        if state is EmailDomainPolicyRolloutState.ACTIVE:
            # The only phase where the resolved value is itself the answer.
            return resolved
        if state is EmailDomainPolicyRolloutState.COMPATIBILITY:
            # The cached mode/lists describe the imported row; the legacy triple
            # is what old replicas are enforcing, so it governs here too.
            return await self._legacy.read_snapshot()
        # ROLLBACK_FROZEN: read the authority directly rather than serve a cached
        # snapshot while the rollback rewrites Redis beside it. A bounded operator
        # window, not a steady state.
        frozen = await self._repository.get()
        if frozen is None:
            raise EmailDomainPolicyUnavailable("policy row disappeared during rollback preparation")
        return frozen

    async def _resolve_state_carrier(self) -> EmailDomainPolicy:
        """Process cache, then mirror, then the database — in that order."""
        global _entry

        now = time.monotonic()
        cached = _entry
        if cached is not None and cached.expires_at > now:
            return cached.policy

        mirrored = await self._read_mirror()
        # A non-positive remaining life is a miss, not a value to serve once.
        # The adapter already rejects those, and this repeats the rule because
        # serving a snapshot whose freshness cannot be bounded is exactly the
        # unbounded staleness the whole TTL contract exists to prevent — it must
        # not depend on one adapter getting it right.
        if mirrored is not None and mirrored.ttl_seconds > 0:
            # Capped to the mirror's remaining life, never extended past it, and
            # the mirror is deliberately not rewritten: renewing a TTL from the
            # value it is bounding would make a stale snapshot immortal.
            lifetime = min(LOCAL_CACHE_TTL_SECONDS, mirrored.ttl_seconds)
            _entry = _Entry(policy=mirrored.policy, expires_at=now + lifetime)
            return mirrored.policy

        stored = await self._read_repository()
        if stored is None:
            # No row means bootstrap has not run, or has been rolled back beneath
            # a running process. Either way there is no authority to consult, and
            # inventing one would be inventing a policy.
            raise EmailDomainPolicyUnavailable("no email-domain policy row exists")
        _entry = _Entry(policy=stored, expires_at=now + LOCAL_CACHE_TTL_SECONDS)
        # Best-effort, and only from here: a database read is the one source
        # entitled to set the mirror's TTL.
        try:
            await self._mirror.write(stored)
        except Exception:  # pragma: no cover - repair is best-effort by design
            logger.bind(event="email_domain_policy_mirror_write_failed").warning(
                "could not refresh the email-domain policy mirror; reads fall back to the database",
                exc_info=True,
            )
        return stored

    async def _read_mirror(self) -> MirroredPolicy | None:
        try:
            return await self._mirror.read()
        except Exception:
            # An unreachable or erroring mirror is a cache miss, not a failure:
            # the database is still there and is the authority.
            logger.bind(event="email_domain_policy_mirror_read_failed").warning(
                "email-domain policy mirror unreadable; falling back to the database",
                exc_info=True,
            )
            return None

    async def _read_repository(self) -> EmailDomainPolicy | None:
        try:
            return await self._repository.get()
        except Exception as exc:
            # Both stores are gone. Fail closed and loudly rather than admit
            # every domain — this is the condition the predecessor read as `off`.
            raise EmailDomainPolicyUnavailable("the email-domain policy authority is unreachable") from exc


__all__ = [
    "LOCAL_CACHE_TTL_SECONDS",
    "EmailDomainPolicyReader",
    "reset_process_cache",
]
