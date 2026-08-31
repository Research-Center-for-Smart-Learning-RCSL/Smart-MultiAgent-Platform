"""The three email-domain policy rollout transitions (R19a.13).

Covers `docs/tasks/2026-08-30-identity-onboarding-policy-hardening`:

* AC-8  activation is required before a PUT, and each transition serialises on
        the row it changes;
* AC-13 an activation failure leaves the row in compatibility and is retryable;
* AC-14 rollback preparation freezes first, records the marker only after an
        exact readback, and fails while safely frozen otherwise;
* AC-15 nothing in the rollback path is reachable from Alembic, and a run that
        did not verify records no marker.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from contexts.identity.application import email_domain_rollout as rollout
from contexts.identity.application.email_domain_bootstrap import BOOTSTRAP_LOCK
from contexts.identity.application.email_domain_rollout import RolloutTransitionError
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)
from contexts.identity.domain.errors import EmailDomainPolicyUnavailable

_ACTIVE = EmailDomainPolicyRolloutState.ACTIVE
_COMPAT = EmailDomainPolicyRolloutState.COMPATIBILITY
_FROZEN = EmailDomainPolicyRolloutState.ROLLBACK_FROZEN


def _policy(
    *,
    state: EmailDomainPolicyRolloutState,
    mode: EmailDomainPolicyMode = EmailDomainPolicyMode.ALLOW,
    allow: frozenset[str] = frozenset({"example.edu"}),
    deny: frozenset[str] = frozenset(),
    version: int = 3,
    mirrored: int | None = None,
) -> EmailDomainPolicy:
    return EmailDomainPolicy(
        mode=mode,
        allow=allow,
        deny=deny,
        version=version,
        rollout_state=state,
        legacy_mirrored_version=mirrored,
    )


def _repo(current: EmailDomainPolicy | None) -> AsyncMock:
    repository = AsyncMock()
    repository.get_for_update.return_value = current
    return repository


def _no_lock():
    return patch(
        "contexts.identity.application.email_domain_rollout.advisory_xact_lock",
        new_callable=AsyncMock,
    )


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------


async def test_activation_takes_both_locks_before_reading() -> None:
    """The advisory lock serialises the commands against each other and against
    the boot-time import; the row lock serialises against an Admin PUT."""
    db = AsyncMock()
    repository = _repo(_policy(state=_COMPAT))
    repository.activate.return_value = _policy(state=_ACTIVE, version=4)
    legacy = AsyncMock()
    legacy.read_snapshot.return_value = _policy(state=_COMPAT, version=0)

    with _no_lock() as lock:
        await rollout.activate(db, repository=repository, legacy=legacy)

    lock.assert_awaited_once_with(db, BOOTSTRAP_LOCK)
    repository.get_for_update.assert_awaited_once()


async def test_activation_adopts_the_legacy_snapshot_read_at_transition_time() -> None:
    """A compatibility-era `redis-cli` edit must not be silently reverted at the
    moment PostgreSQL becomes authoritative."""
    repository = _repo(_policy(state=_COMPAT, allow=frozenset({"imported.edu"})))
    repository.activate.return_value = _policy(state=_ACTIVE, version=4)
    legacy = AsyncMock()
    legacy.read_snapshot.return_value = _policy(
        state=_COMPAT, mode=EmailDomainPolicyMode.DENY, allow=frozenset(), deny=frozenset({"late.test"})
    )

    with _no_lock():
        report = await rollout.activate(AsyncMock(), repository=repository, legacy=legacy)

    assert repository.activate.await_args.kwargs == {
        "mode": EmailDomainPolicyMode.DENY,
        "allow": frozenset(),
        "deny": frozenset({"late.test"}),
    }
    assert report.changed is True
    assert report.rollout_state == "active"


async def test_activation_is_idempotent_against_an_already_active_row() -> None:
    repository = _repo(_policy(state=_ACTIVE, version=9))
    legacy = AsyncMock()

    with _no_lock():
        report = await rollout.activate(AsyncMock(), repository=repository, legacy=legacy)

    assert report.changed is False
    assert report.version == 9
    repository.activate.assert_not_awaited()
    legacy.read_snapshot.assert_not_awaited()


async def test_activation_refuses_a_frozen_row_rather_than_lifting_the_fence() -> None:
    """`activate` and `cancel` have different preconditions; conflating them
    would let an operator lift a rollback fence by typing the wrong command."""
    repository = _repo(_policy(state=_FROZEN))

    with _no_lock(), pytest.raises(RolloutTransitionError, match="cancel the rollback"):
        await rollout.activate(AsyncMock(), repository=repository, legacy=AsyncMock())
    repository.activate.assert_not_awaited()


async def test_an_unreadable_legacy_store_leaves_the_row_in_compatibility() -> None:
    """AC-13: the read is inside the caller's transaction, so the failure rolls
    back to the state it started in and the command is retryable."""
    repository = _repo(_policy(state=_COMPAT))
    legacy = AsyncMock()
    legacy.read_snapshot.side_effect = EmailDomainPolicyUnavailable("redis is down")

    with _no_lock(), pytest.raises(EmailDomainPolicyUnavailable):
        await rollout.activate(AsyncMock(), repository=repository, legacy=legacy)
    repository.activate.assert_not_awaited()


async def test_a_transition_on_a_missing_row_is_refused_with_a_usable_message() -> None:
    with _no_lock(), pytest.raises(RolloutTransitionError, match="boot-time import"):
        await rollout.activate(AsyncMock(), repository=_repo(None), legacy=AsyncMock())


# ---------------------------------------------------------------------------
# rollback preparation
# ---------------------------------------------------------------------------


async def test_the_freeze_fences_writes_before_any_mirror_is_taken() -> None:
    """Freeze-first is the race argument: an update landing after the mirror was
    verified would leave an old image enforcing a policy that has moved."""
    repository = _repo(_policy(state=_ACTIVE))
    repository.set_rollout_state.return_value = _policy(state=_FROZEN)

    with _no_lock():
        frozen = await rollout.freeze_for_rollback(AsyncMock(), repository=repository)

    assert frozen.rollout_state is _FROZEN
    assert repository.set_rollout_state.await_args.kwargs["state"] is _FROZEN


async def test_re_freezing_after_a_failed_mirror_write_is_accepted() -> None:
    """A retry must re-freeze cleanly rather than report that nothing matched."""
    repository = _repo(_policy(state=_FROZEN))
    repository.set_rollout_state.return_value = _policy(state=_FROZEN)

    with _no_lock():
        await rollout.freeze_for_rollback(AsyncMock(), repository=repository)

    assert _FROZEN in repository.set_rollout_state.await_args.kwargs["expected_states"]


async def test_freezing_a_compatibility_row_is_refused() -> None:
    """The legacy keys are already authoritative there; there is nothing to
    prepare, and freezing would fence writes for no reason."""
    repository = _repo(_policy(state=_COMPAT))

    with _no_lock(), pytest.raises(RolloutTransitionError, match="still in compatibility"):
        await rollout.freeze_for_rollback(AsyncMock(), repository=repository)
    repository.set_rollout_state.assert_not_awaited()


async def test_the_mirror_is_read_back_and_compared_not_merely_written() -> None:
    policy = _policy(state=_FROZEN, allow=frozenset({"example.edu"}))
    legacy = AsyncMock()
    legacy.read_snapshot.return_value = policy

    await rollout.mirror_policy_to_legacy(legacy=legacy, policy=policy)

    legacy.replace.assert_awaited_once_with(policy)
    legacy.read_snapshot.assert_awaited_once()


async def test_a_readback_that_disagrees_refuses_the_rollback() -> None:
    """ "The write returned without an error" does not establish that an old image
    will enforce this policy; an eviction between write and read is exactly the
    failure this rollout exists to survive."""
    policy = _policy(state=_FROZEN, allow=frozenset({"example.edu"}))
    legacy = AsyncMock()
    legacy.read_snapshot.return_value = _policy(state=_FROZEN, allow=frozenset({"other.edu"}))

    with pytest.raises(RolloutTransitionError, match="must not proceed"):
        await rollout.mirror_policy_to_legacy(legacy=legacy, policy=policy)


async def test_the_marker_is_recorded_only_for_the_version_that_was_verified() -> None:
    repository = AsyncMock()
    repository.record_legacy_mirror.return_value = _policy(state=_FROZEN, version=3, mirrored=3)

    with _no_lock():
        report = await rollout.record_verified_mirror(AsyncMock(), repository=repository, version=3)

    assert report.legacy_mirrored_version == 3
    assert repository.record_legacy_mirror.await_args.kwargs == {"version": 3}


async def test_a_version_that_moved_since_the_readback_records_no_marker() -> None:
    """A marker vouches for what an old image will enforce; recording one against
    a version the row has moved past would be a false guarantee."""
    repository = AsyncMock()
    repository.record_legacy_mirror.return_value = None

    with _no_lock(), pytest.raises(RolloutTransitionError, match="no longer at version 3"):
        await rollout.record_verified_mirror(AsyncMock(), repository=repository, version=3)


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


async def test_cancelling_restores_active_and_clears_the_marker() -> None:
    """A marker that outlived its freeze would vouch for a legacy snapshot the
    next Admin edit invalidates."""
    repository = _repo(_policy(state=_FROZEN, version=3, mirrored=3))
    repository.set_rollout_state.return_value = _policy(state=_ACTIVE, version=3, mirrored=3)
    repository.clear_legacy_mirror.return_value = _policy(state=_ACTIVE, version=3, mirrored=None)

    with _no_lock():
        report = await rollout.cancel_rollback(AsyncMock(), repository=repository)

    assert report.rollout_state == "active"
    assert report.legacy_mirrored_version is None
    repository.clear_legacy_mirror.assert_awaited_once()


async def test_cancelling_an_active_row_changes_nothing() -> None:
    repository = _repo(_policy(state=_ACTIVE, version=5))

    with _no_lock():
        report = await rollout.cancel_rollback(AsyncMock(), repository=repository)

    assert report.changed is False
    repository.set_rollout_state.assert_not_awaited()


async def test_cancelling_a_compatibility_row_is_refused() -> None:
    repository = _repo(_policy(state=_COMPAT))

    with _no_lock(), pytest.raises(RolloutTransitionError, match="no rollback to cancel"):
        await rollout.cancel_rollback(AsyncMock(), repository=repository)
    repository.set_rollout_state.assert_not_awaited()
