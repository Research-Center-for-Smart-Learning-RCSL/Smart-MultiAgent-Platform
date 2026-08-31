"""The three explicit rollout transitions for the email-domain policy (R19a.13).

PostgreSQL and the three legacy Redis keys cannot commit together, so the change
of authority is staged rather than atomic, and each stage is an operator decision
rather than an incidental startup side effect:

* **activate** — `compatibility` to `active`. The operator asserts that every
  replica running the previous image has drained. Nothing here can verify that;
  automatic replica discovery is out of scope, so the command says so and the
  assertion is the operator's.
* **freeze / mirror / mark** — `active` to `rollback_frozen`, then the legacy
  triple is rewritten from the frozen policy and read back, and only an exact
  readback records `legacy_mirrored_version`. Freezing *first* is what makes it
  race-free: Admin writes and this transition serialise on the same row, so the
  snapshot cannot go stale between being verified and being relied on.
* **cancel** — `rollback_frozen` back to `active`, once the old images are gone
  again. Its own command rather than a second meaning for `activate`: the two
  have different preconditions, and on an operator surface the wrong command is
  the expensive mistake.

Every transition is guarded on the state it expects, so re-running one after a
partial failure either matches nothing (and reports what it found) or re-applies
the same committed decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.application.email_domain_bootstrap import BOOTSTRAP_LOCK
from contexts.identity.application.ports import (
    EmailDomainPolicyRepository,
    LegacyEmailDomainPolicyStore,
)
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyRolloutState,
)
from shared_kernel.db.advisory_lock import advisory_xact_lock


class RolloutTransitionError(RuntimeError):
    """A transition could not be completed safely.

    Deliberately not an ``IdentityError``: no HTTP route can raise it, and
    registering it in the RFC-7807 map would imply one could.
    """


@dataclass(frozen=True, slots=True)
class TransitionReport:
    """What an operator needs printed after a transition.

    ``changed`` distinguishes "this run made the change" from "it was already
    so", which is the difference between a successful transition and a
    successful *re-run* — an operator retrying after a failure needs to know
    which one they got.
    """

    rollout_state: str
    version: int
    legacy_mirrored_version: int | None
    changed: bool


def _report(policy: EmailDomainPolicy, *, changed: bool) -> TransitionReport:
    return TransitionReport(
        rollout_state=policy.rollout_state.value,
        version=policy.version,
        legacy_mirrored_version=policy.legacy_mirrored_version,
        changed=changed,
    )


async def _locked_policy(db: AsyncSession, repository: EmailDomainPolicyRepository) -> EmailDomainPolicy:
    # Both locks, and both earn their place: the advisory lock serialises the
    # maintenance commands against each other and against the boot-time import,
    # while the row lock serialises this read-then-write against an Admin PUT.
    await advisory_xact_lock(db, BOOTSTRAP_LOCK)
    policy = await repository.get_for_update()
    if policy is None:
        raise RolloutTransitionError(
            "no email-domain policy row exists; start the application once so the "
            "boot-time import can create it"
        )
    return policy


async def activate(
    db: AsyncSession,
    *,
    repository: EmailDomainPolicyRepository,
    legacy: LegacyEmailDomainPolicyStore,
) -> TransitionReport:
    """Make PostgreSQL authoritative, adopting one final legacy snapshot.

    Run only once every replica of the previous image has drained. Until then
    those replicas enforce the legacy triple and know nothing about the row, so
    activating early means two versions enforcing two policies.
    """
    policy = await _locked_policy(db, repository)
    if policy.rollout_state is EmailDomainPolicyRolloutState.ACTIVE:
        return _report(policy, changed=False)
    if policy.rollout_state is EmailDomainPolicyRolloutState.ROLLBACK_FROZEN:
        raise RolloutTransitionError(
            "the policy is frozen for rollback; cancel the rollback instead of activating"
        )

    # Read the legacy triple *now*, not at boot: a compatibility deployment's
    # operator may have edited it with `redis-cli` since the import, and
    # activating onto the older imported values would silently revert that edit
    # at the moment PostgreSQL becomes authoritative.
    snapshot = await legacy.read_snapshot()
    activated = await repository.activate(mode=snapshot.mode, allow=snapshot.allow, deny=snapshot.deny)
    if activated is None:  # pragma: no cover - the row lock makes this unreachable
        raise RolloutTransitionError("the rollout state changed while activating; re-run")
    return _report(activated, changed=True)


async def freeze_for_rollback(
    db: AsyncSession, *, repository: EmailDomainPolicyRepository
) -> EmailDomainPolicy:
    """Fence Admin writes before the legacy mirror is taken.

    Freeze-first is the whole race argument: an update that lands after the
    mirror is written and verified would leave an operator starting an old image
    on a policy the mirror no longer reflects. Freezing first means such an
    update is rejected instead.
    """
    policy = await _locked_policy(db, repository)
    if policy.rollout_state is EmailDomainPolicyRolloutState.COMPATIBILITY:
        raise RolloutTransitionError(
            "the policy is still in compatibility; the legacy keys are already authoritative "
            "and no rollback preparation is needed"
        )
    frozen = await repository.set_rollout_state(
        state=EmailDomainPolicyRolloutState.ROLLBACK_FROZEN,
        expected_states=(
            EmailDomainPolicyRolloutState.ACTIVE,
            # Included so a retry after a failed mirror write re-freezes cleanly
            # rather than reporting that nothing matched.
            EmailDomainPolicyRolloutState.ROLLBACK_FROZEN,
        ),
    )
    if frozen is None:  # pragma: no cover - the row lock makes this unreachable
        raise RolloutTransitionError("the rollout state changed while freezing; re-run")
    return frozen


async def mirror_policy_to_legacy(*, legacy: LegacyEmailDomainPolicyStore, policy: EmailDomainPolicy) -> None:
    """Write the frozen policy into the legacy triple and read it back.

    The readback is not ceremony. The whole point of the marker recorded next is
    that an old image will enforce exactly this policy, and "the write returned
    without an error" does not establish that — an eviction between the write and
    the read is precisely the failure mode this rollout exists to survive.
    """
    await legacy.replace(policy)
    readback = await legacy.read_snapshot()
    if (readback.mode, readback.allow, readback.deny) != (policy.mode, policy.allow, policy.deny):
        raise RolloutTransitionError(
            "the legacy email-domain keys do not match the frozen policy after writing them; "
            "the policy remains frozen and the rollback must not proceed"
        )


async def record_verified_mirror(
    db: AsyncSession, *, repository: EmailDomainPolicyRepository, version: int
) -> TransitionReport:
    """Record that ``version`` is the version the legacy triple now carries."""
    await advisory_xact_lock(db, BOOTSTRAP_LOCK)
    marked = await repository.record_legacy_mirror(version=version)
    if marked is None:
        raise RolloutTransitionError(
            f"the policy is no longer at version {version}; the verified legacy snapshot is "
            "stale and the marker was not recorded"
        )
    return _report(marked, changed=True)


async def cancel_rollback(db: AsyncSession, *, repository: EmailDomainPolicyRepository) -> TransitionReport:
    """Lift the write fence once the old images are gone again.

    Clearing `legacy_mirrored_version` is the point of the command as much as the
    state change: a marker that outlived its freeze would vouch for a legacy
    snapshot that the next Admin edit immediately invalidates.
    """
    policy = await _locked_policy(db, repository)
    if policy.rollout_state is EmailDomainPolicyRolloutState.ACTIVE:
        return _report(policy, changed=False)
    if policy.rollout_state is EmailDomainPolicyRolloutState.COMPATIBILITY:
        raise RolloutTransitionError(
            "the policy is in compatibility, not frozen; there is no rollback to cancel"
        )
    restored = await repository.set_rollout_state(
        state=EmailDomainPolicyRolloutState.ACTIVE,
        expected_states=(EmailDomainPolicyRolloutState.ROLLBACK_FROZEN,),
    )
    if restored is None:  # pragma: no cover - the row lock makes this unreachable
        raise RolloutTransitionError("the rollout state changed while cancelling; re-run")
    cleared = await repository.clear_legacy_mirror()
    return _report(cleared or restored, changed=True)


__all__ = [
    "RolloutTransitionError",
    "TransitionReport",
    "activate",
    "cancel_rollback",
    "freeze_for_rollback",
    "mirror_policy_to_legacy",
    "record_verified_mirror",
]
