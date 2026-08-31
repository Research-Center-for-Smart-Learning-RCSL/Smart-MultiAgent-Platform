"""Rollout transitions for the email-domain policy (R19a.13).

Session and store wiring for the three transitions in
`contexts.identity.application.email_domain_rollout`; the decisions themselves
live there, where they can be tested without PostgreSQL or Redis.

**Why the rollback preparation spans three transactions.** The freeze must be
committed before the legacy triple is written, or an Admin update could land
between the write and the readback and leave the verified mirror describing a
policy that no longer exists. The marker must be recorded after the readback, or
it would vouch for a write that had not been checked. Redis is not
transactional with PostgreSQL, so the boundaries are where the ordering
guarantees are, not where a single transaction would have been tidier.

**Arming is an environment variable, not a CLI flag**, matching
`purge_session_dirs` — see its `_ARMED_ENV` for the typer/click flag-conversion
defect that made a flag the unsafe choice. Activation carries the operator's
assertion that every replica of the previous image has drained, which nothing
here can verify; the environment variable is where that assertion is made.
"""

from __future__ import annotations

import os
from typing import Final

from loguru import logger

from contexts.identity.application import email_domain_rollout as rollout
from contexts.identity.application.email_domain_rollout import (
    RolloutTransitionError,
    TransitionReport,
)
from contexts.identity.infrastructure.email_domain_legacy import (
    RedisLegacyEmailDomainPolicyStore,
)
from contexts.identity.infrastructure.email_domain_mirror import RedisEmailDomainPolicyMirror
from contexts.identity.infrastructure.email_domain_repository import EmailDomainPolicyRepository
from shared_kernel.db.session import async_session

ACTIVATE_ARMED_ENV: Final = "SMAP_ACTIVATE_EMAIL_DOMAIN_POLICY_ARMED"
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


def activation_armed() -> bool:
    return os.getenv(ACTIVATE_ARMED_ENV, "").strip().lower() in _TRUTHY


async def _drop_mirror() -> None:
    """Delete the v2 cache so the transition takes effect on the next request.

    Best-effort by design: the mirror's 30-second TTL already bounds how long a
    stale phase can be served, so a failure here delays the change rather than
    breaking it, and must not fail a transition that has already committed.
    """
    try:
        await RedisEmailDomainPolicyMirror().delete()
    except Exception:
        logger.bind(event="email_domain_policy_mirror_delete_failed").warning(
            "could not drop the email-domain policy mirror; the transition takes effect "
            "within its 30 s TTL instead of on the next request",
            exc_info=True,
        )


async def activate() -> TransitionReport:
    """`compatibility` -> `active`, adopting one final legacy snapshot."""
    async with async_session() as db, db.begin():
        report = await rollout.activate(
            db,
            repository=EmailDomainPolicyRepository(db),
            legacy=RedisLegacyEmailDomainPolicyStore(),
        )
    await _drop_mirror()
    return report


async def prepare_rollback() -> TransitionReport:
    """`active` -> `rollback_frozen`, then mirror the legacy triple and verify it."""
    async with async_session() as db, db.begin():
        frozen = await rollout.freeze_for_rollback(db, repository=EmailDomainPolicyRepository(db))
    # The freeze is committed, so a failure below leaves the policy frozen and
    # unmirrored: safe (writes stay fenced, new readers keep reading PostgreSQL)
    # and safe to retry. What it must never do is leave a *marker* behind.
    await _drop_mirror()

    await rollout.mirror_policy_to_legacy(legacy=RedisLegacyEmailDomainPolicyStore(), policy=frozen)

    async with async_session() as db, db.begin():
        # Returning from inside the block is deliberate: `db.begin()` commits on
        # exit either way, and a failed commit must discard the report rather
        # than let the caller print a marker that was not stored.
        return await rollout.record_verified_mirror(
            db, repository=EmailDomainPolicyRepository(db), version=frozen.version
        )


async def cancel_rollback() -> TransitionReport:
    """`rollback_frozen` -> `active`, clearing the now-meaningless marker."""
    async with async_session() as db, db.begin():
        report = await rollout.cancel_rollback(db, repository=EmailDomainPolicyRepository(db))
    await _drop_mirror()
    return report


__all__ = [
    "ACTIVATE_ARMED_ENV",
    "RolloutTransitionError",
    "TransitionReport",
    "activate",
    "activation_armed",
    "cancel_rollback",
    "prepare_rollback",
]
