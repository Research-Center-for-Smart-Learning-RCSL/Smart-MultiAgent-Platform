"""Ports the identity application layer depends on (R19a.13).

These exist so `EmailDomainPolicyReader` and `EmailDomainPolicyService` can be
written against behaviour rather than against `contexts.identity.infrastructure`,
and so a unit test can supply a store without a live PostgreSQL or Redis. The
concrete adapters live in `infrastructure/` and are wired by
`application/factory.py`; nothing in this module knows about SQLAlchemy or Redis.

The rest of the identity application layer still imports infrastructure directly
(the dossier's §9 records that debt and its FU-3). This is the minimum seam this
change needs, deliberately not a general cleanup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)


@dataclass(frozen=True, slots=True)
class MirroredPolicy:
    """A policy read out of the acceleration mirror, with its remaining life.

    ``ttl_seconds`` is the mirror's own remaining PTTL, read in the same round
    trip as the value. A caller may hold this snapshot in process for at most
    that long: a local cache that outlived the mirror would turn a non-renewable
    30-second bound back into an unbounded stale value, which is the failure the
    versioned mirror exists to end (AC-12).
    """

    policy: EmailDomainPolicy
    ttl_seconds: float


class EmailDomainPolicyRepository(Protocol):
    """The durable authority. One row, guarded by an integer version."""

    async def get(self) -> EmailDomainPolicy | None:
        """The stored singleton, or None when bootstrap has not run."""
        ...

    async def create_from_legacy(self, policy: EmailDomainPolicy) -> EmailDomainPolicy:
        """Insert version 1 in `compatibility`. Caller holds the bootstrap lock."""
        ...

    async def replace_active(
        self,
        *,
        expected_version: int,
        mode: EmailDomainPolicyMode,
        allow: frozenset[str],
        deny: frozenset[str],
        actor_user_id: uuid.UUID,
    ) -> EmailDomainPolicy | None:
        """Full-replace the row if it is `active` and still at ``expected_version``.

        Returns None when the guard did not match, which the service maps to a
        409 rather than blind-overwriting an edit made since the form loaded.
        """
        ...

    # ----- rollout transitions -------------------------------------------
    #
    # Operator-only. Each is guarded on the state it expects, so a re-run after a
    # partial failure either re-applies the same committed decision or matches
    # nothing and reports what it found.

    async def get_for_update(self) -> EmailDomainPolicy | None:
        """The row, locked for the rest of the caller's transaction.

        A transition reads the policy and then writes a decision derived from it;
        a plain read would let an Admin update land in between.
        """
        ...

    async def activate(
        self,
        *,
        mode: EmailDomainPolicyMode,
        allow: frozenset[str],
        deny: frozenset[str],
    ) -> EmailDomainPolicy | None:
        """`compatibility` -> `active`, adopting a final legacy snapshot."""
        ...

    async def set_rollout_state(
        self,
        *,
        state: EmailDomainPolicyRolloutState,
        expected_states: tuple[EmailDomainPolicyRolloutState, ...],
    ) -> EmailDomainPolicy | None:
        """Move to ``state`` if the row is in one of ``expected_states``."""
        ...

    async def record_legacy_mirror(self, *, version: int) -> EmailDomainPolicy | None:
        """Mark ``version`` as written and read back into the legacy triple.

        Guarded on the version still being ``version``, so a marker can never
        vouch for a snapshot the row has since moved past.
        """
        ...

    async def clear_legacy_mirror(self) -> EmailDomainPolicy | None:
        """Drop the rollback marker when the freeze is lifted."""
        ...


class EmailDomainPolicyMirror(Protocol):
    """The disposable v2 acceleration cache. Never an authority."""

    async def read(self) -> MirroredPolicy | None:
        """Value and remaining PTTL together, or None when missing/malformed.

        Malformed includes an absent or unrecognised ``rollout_state``: a reader
        that guessed a phase would enforce one version's policy under another
        version's authority (AC-11).
        """
        ...

    async def write(self, policy: EmailDomainPolicy) -> None:
        """Best-effort repair from a *database* read, with a fresh 30 s TTL.

        Only a database read may call this. Re-writing a Redis-derived snapshot
        would renew the TTL from the cache itself, and a valid-but-stale value
        would then live forever.
        """
        ...

    async def delete(self) -> None:
        """Drop the key so the next reader misses and re-reads the authority.

        Called by a rollout transition, so an operator-visible change takes
        effect on the next request rather than after a TTL. A failure here is
        logged and not fatal — the 30-second TTL still bounds it.
        """
        ...


class LegacyEmailDomainPolicyStore(Protocol):
    """The three unversioned Redis keys the pre-R19a.13 reader used.

    A rollout/rollback bridge only. It is removed once every supported deployment
    is past the compatibility version (the dossier's FU-4).
    """

    async def read_snapshot(self) -> EmailDomainPolicy:
        """One atomic, classified read of the legacy triple.

        Raises ``InvalidLegacyEmailDomainPolicy`` for a distinguishable corrupt
        shape and ``EmailDomainPolicyUnavailable`` when Redis cannot be read.
        """
        ...

    async def replace(self, policy: EmailDomainPolicy) -> None:
        """Atomically rewrite all three keys to match ``policy``."""
        ...


__all__ = [
    "EmailDomainPolicyMirror",
    "EmailDomainPolicyRepository",
    "LegacyEmailDomainPolicyStore",
    "MirroredPolicy",
]
