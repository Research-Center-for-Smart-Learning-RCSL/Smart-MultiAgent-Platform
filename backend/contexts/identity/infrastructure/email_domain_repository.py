"""PostgreSQL authority for the email-domain policy singleton (R19a.13).

Follows `ActivityPolicyRepository`'s guarded-update shape: the version guard is
part of the ``WHERE``, so a losing concurrent write matches no row and is
reported as a conflict rather than silently overwriting the winner.

The rollout state is part of the same guard on purpose. A write fenced by phase
and a write fenced by version have to serialise on one row, or a rollback
preparation that has just frozen the policy could be followed by an Admin update
that makes the mirror it verified stale.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)
from contexts.identity.infrastructure import tables as t
from shared_kernel.auth.clients import now

_COLS = (
    t.email_domain_policies.c.mode,
    t.email_domain_policies.c.rollout_state,
    t.email_domain_policies.c.allow_domains,
    t.email_domain_policies.c.deny_domains,
    t.email_domain_policies.c.version,
    t.email_domain_policies.c.legacy_mirrored_version,
    t.email_domain_policies.c.updated_at,
    t.email_domain_policies.c.updated_by_user_id,
)


def row_to_policy(row: Any) -> EmailDomainPolicy:
    return EmailDomainPolicy(
        mode=EmailDomainPolicyMode(row.mode),
        allow=frozenset(row.allow_domains),
        deny=frozenset(row.deny_domains),
        version=row.version,
        rollout_state=EmailDomainPolicyRolloutState(row.rollout_state),
        legacy_mirrored_version=row.legacy_mirrored_version,
        updated_at=row.updated_at,
        updated_by_user_id=row.updated_by_user_id,
    )


class EmailDomainPolicyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self) -> EmailDomainPolicy | None:
        row = (
            await self._db.execute(
                sa.select(*_COLS).where(t.email_domain_policies.c.id == t.EMAIL_DOMAIN_POLICY_ID)
            )
        ).first()
        return row_to_policy(row) if row is not None else None

    async def get_for_update(self) -> EmailDomainPolicy | None:
        """The row, locked for the rest of the caller's transaction.

        Used by the rollout transitions, which read the policy and then write a
        decision derived from it. A plain read there would let an Admin update
        land in between, and the transition would commit a decision about a
        version that no longer exists.
        """
        row = (
            await self._db.execute(
                sa.select(*_COLS)
                .where(t.email_domain_policies.c.id == t.EMAIL_DOMAIN_POLICY_ID)
                .with_for_update()
            )
        ).first()
        return row_to_policy(row) if row is not None else None

    async def activate(
        self, *, mode: EmailDomainPolicyMode, allow: frozenset[str], deny: frozenset[str]
    ) -> EmailDomainPolicy | None:
        """Switch authority to PostgreSQL, adopting a final legacy snapshot.

        The lists are written as part of the transition rather than left as the
        boot-time import: a compatibility deployment's operator may have edited
        the legacy triple with `redis-cli` in the meantime, and activating onto
        the older imported values would silently revert that edit at the moment
        PostgreSQL becomes authoritative.

        Guarded on `compatibility`, so re-running it against an already-active
        row matches nothing and the command reports the state it found.
        """
        result = await self._db.execute(
            t.email_domain_policies.update()
            .where(
                sa.and_(
                    t.email_domain_policies.c.id == t.EMAIL_DOMAIN_POLICY_ID,
                    t.email_domain_policies.c.rollout_state
                    == EmailDomainPolicyRolloutState.COMPATIBILITY.value,
                )
            )
            .values(
                mode=mode.value,
                allow_domains=sorted(allow),
                deny_domains=sorted(deny),
                rollout_state=EmailDomainPolicyRolloutState.ACTIVE.value,
                version=t.email_domain_policies.c.version + 1,
                legacy_mirrored_version=None,
                updated_at=now(),
            )
            .returning(*_COLS)
        )
        row = result.first()
        return row_to_policy(row) if row is not None else None

    async def create_from_legacy(self, policy: EmailDomainPolicy) -> EmailDomainPolicy:
        """Insert the imported snapshot as version 1 in `compatibility`.

        No ``ON CONFLICT`` clause: the caller holds the bootstrap advisory lock
        and has re-read under it, so a conflict here would mean the lock is not
        doing its job and should surface rather than be swallowed. The pinned
        primary key makes a second row impossible either way.
        """
        row = (
            await self._db.execute(
                t.email_domain_policies.insert()
                .values(
                    id=t.EMAIL_DOMAIN_POLICY_ID,
                    mode=policy.mode.value,
                    rollout_state=EmailDomainPolicyRolloutState.COMPATIBILITY.value,
                    allow_domains=sorted(policy.allow),
                    deny_domains=sorted(policy.deny),
                    version=1,
                    legacy_mirrored_version=None,
                    updated_by_user_id=None,
                )
                .returning(*_COLS)
            )
        ).first()
        if row is None:  # pragma: no cover - RETURNING on a successful insert
            raise RuntimeError("insert returned no row")
        return row_to_policy(row)

    async def replace_active(
        self,
        *,
        expected_version: int,
        mode: EmailDomainPolicyMode,
        allow: frozenset[str],
        deny: frozenset[str],
        actor_user_id: uuid.UUID,
    ) -> EmailDomainPolicy | None:
        """Full-replace the policy, guarded on both version and phase.

        Returns None when neither matched. The service distinguishes the two by
        re-reading, because "somebody edited it first" and "writes are fenced"
        are different problems with different recoveries.
        """
        result = await self._db.execute(
            t.email_domain_policies.update()
            .where(
                sa.and_(
                    t.email_domain_policies.c.id == t.EMAIL_DOMAIN_POLICY_ID,
                    t.email_domain_policies.c.version == expected_version,
                    t.email_domain_policies.c.rollout_state == EmailDomainPolicyRolloutState.ACTIVE.value,
                )
            )
            .values(
                mode=mode.value,
                allow_domains=sorted(allow),
                deny_domains=sorted(deny),
                version=t.email_domain_policies.c.version + 1,
                # An Admin edit invalidates any rollback marker: the legacy
                # mirror was verified against a version that is no longer the
                # stored one, so leaving it would let an operator start an old
                # image on a policy that has since moved.
                legacy_mirrored_version=None,
                updated_at=now(),
                updated_by_user_id=actor_user_id,
            )
            .returning(*_COLS)
        )
        row = result.first()
        return row_to_policy(row) if row is not None else None

    async def set_rollout_state(
        self,
        *,
        state: EmailDomainPolicyRolloutState,
        expected_states: tuple[EmailDomainPolicyRolloutState, ...],
    ) -> EmailDomainPolicy | None:
        """Move the row to ``state`` if it is currently in one of ``expected_states``.

        Idempotent by construction when the target is included in the expected
        set: re-running a transition that already committed matches and returns
        the row unchanged, which is what makes the maintenance commands safe to
        retry after a partial failure.
        """
        result = await self._db.execute(
            t.email_domain_policies.update()
            .where(
                sa.and_(
                    t.email_domain_policies.c.id == t.EMAIL_DOMAIN_POLICY_ID,
                    t.email_domain_policies.c.rollout_state.in_([s.value for s in expected_states]),
                )
            )
            .values(rollout_state=state.value, updated_at=now())
            .returning(*_COLS)
        )
        row = result.first()
        return row_to_policy(row) if row is not None else None

    async def record_legacy_mirror(self, *, version: int) -> EmailDomainPolicy | None:
        """Mark ``version`` as written and read back into the legacy triple.

        Guarded on the version still being ``version``: if an update landed
        between the mirror write and this call, the marker would vouch for a
        snapshot that no longer matches the row.
        """
        result = await self._db.execute(
            t.email_domain_policies.update()
            .where(
                sa.and_(
                    t.email_domain_policies.c.id == t.EMAIL_DOMAIN_POLICY_ID,
                    t.email_domain_policies.c.version == version,
                )
            )
            .values(legacy_mirrored_version=version, updated_at=now())
            .returning(*_COLS)
        )
        row = result.first()
        return row_to_policy(row) if row is not None else None

    async def clear_legacy_mirror(self) -> EmailDomainPolicy | None:
        """Drop the rollback marker when the freeze is lifted.

        A marker that outlived its freeze would vouch for a legacy snapshot that
        the next Admin edit invalidates, which is the one thing an operator
        checking it before starting an old image must be able to trust.
        """
        result = await self._db.execute(
            t.email_domain_policies.update()
            .where(t.email_domain_policies.c.id == t.EMAIL_DOMAIN_POLICY_ID)
            .values(legacy_mirrored_version=None, updated_at=now())
            .returning(*_COLS)
        )
        row = result.first()
        return row_to_policy(row) if row is not None else None


__all__ = ["EmailDomainPolicyRepository", "row_to_policy"]
