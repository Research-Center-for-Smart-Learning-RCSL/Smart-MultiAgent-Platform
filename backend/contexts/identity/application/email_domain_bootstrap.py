"""First-start import of the legacy email-domain policy (R19a.13, Q-3).

Imports one atomic snapshot of the three legacy Redis keys into the PostgreSQL
singleton as version 1, in `compatibility` state. It deliberately does **not**
switch authority: replicas running the previous image still know only the legacy
triple, so making PostgreSQL authoritative here would let two versions enforce
two different policies. An explicit maintenance command activates once the
operator has drained them.

**Fatal by design.** This runs as an ordered startup initializer alongside the
ones whose failures propagate, not like the rate-limit primer that swallows them:
the limiter has compile-time defaults to fall back on, and this has none. A boot
that continued past an unreadable or corrupt legacy policy would come up serving
requests with no policy authority at all.

Everything happens inside one transaction holding one advisory lock, so N
concurrent first starts elect a single winner and any pre-commit failure — a
Redis read error, a rejected shape — leaves no row and is safely retried by the
next start.
"""

from __future__ import annotations

from typing import Final

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.application.ports import (
    EmailDomainPolicyRepository,
    LegacyEmailDomainPolicyStore,
)
from shared_kernel.db.advisory_lock import advisory_xact_lock

#: Every process that might create the first row serialises on this name.
BOOTSTRAP_LOCK: Final = "identity:email-domain-policy-bootstrap"


async def import_legacy_policy_if_absent(
    db: AsyncSession,
    *,
    repository: EmailDomainPolicyRepository,
    legacy: LegacyEmailDomainPolicyStore,
) -> None:
    """Create the version-1 compatibility row, once, if none exists.

    The caller supplies an open transaction; the lock is transaction-scoped, so
    it is held until that transaction ends.
    """
    await advisory_xact_lock(db, BOOTSTRAP_LOCK)
    # Re-read *under* the lock. An unlocked pre-check would be a TOCTOU on the
    # one decision this function makes, and the loser would try to insert over
    # the winner's row.
    existing = await repository.get()
    if existing is not None:
        logger.bind(
            event="email_domain_policy_bootstrap_skipped",
            rollout_state=existing.rollout_state.value,
            version=existing.version,
        ).info("email-domain policy already imported")
        return

    # Inside the lock and the transaction: a failure here rolls back to no row.
    snapshot = await legacy.read_snapshot()
    created = await repository.create_from_legacy(snapshot)
    logger.bind(
        event="email_domain_policy_bootstrap_imported",
        mode=created.mode.value,
        rollout_state=created.rollout_state.value,
        # Counts only — a domain list identifies the institutions a deployment
        # serves, and boot logs are the most widely shipped logs there are.
        allow_count=len(created.allow),
        deny_count=len(created.deny),
    ).info("imported the legacy email-domain policy in compatibility state")


__all__ = ["BOOTSTRAP_LOCK", "import_legacy_policy_if_absent"]
