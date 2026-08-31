"""Email-domain policy resolution and the legacy import matrix (R19a.13).

Covers `docs/tasks/2026-08-30-identity-onboarding-policy-hardening`:

* AC-2  the Q-10 classification matrix — which legacy shapes import and which
        block a boot;
* AC-4  a failed mirror write does not roll the committed policy back;
* AC-8  each rollout phase resolves its own authority;
* AC-11 every mirror failure mode falls back to the database, an unrecognised
        `rollout_state` is malformed rather than defaulted, and losing both
        stores is a typed refusal rather than `off`;
* AC-12 a process cache filled from the mirror never outlives the mirror's PTTL,
        and a mirror-derived read never refreshes the mirror.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from unittest.mock import AsyncMock

import orjson
import pytest

from contexts.identity.application import email_domain_policy_reader as reader_mod
from contexts.identity.application.email_domain_policy_reader import (
    LOCAL_CACHE_TTL_SECONDS,
    EmailDomainPolicyReader,
    reset_process_cache,
)
from contexts.identity.application.ports import MirroredPolicy
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicy,
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)
from contexts.identity.domain.errors import (
    EmailDomainPolicyUnavailable,
    InvalidLegacyEmailDomainPolicy,
)
from contexts.identity.infrastructure.email_domain_legacy import (
    KEY_ALLOW,
    KEY_DENY,
    KEY_MODE,
    classify_legacy_snapshot,
)
from contexts.identity.infrastructure.email_domain_mirror import SCHEMA, _decode

_ACTIVE = EmailDomainPolicyRolloutState.ACTIVE
_COMPAT = EmailDomainPolicyRolloutState.COMPATIBILITY
_FROZEN = EmailDomainPolicyRolloutState.ROLLBACK_FROZEN


@pytest.fixture(autouse=True)
def _clean_process_cache() -> Iterator[None]:
    """The reader's snapshot is process-wide by design, so one test's cache would
    otherwise decide the next test's outcome."""
    reset_process_cache()
    yield
    reset_process_cache()


def _policy(
    *,
    state: EmailDomainPolicyRolloutState = _ACTIVE,
    mode: EmailDomainPolicyMode = EmailDomainPolicyMode.ALLOW,
    allow: frozenset[str] = frozenset({"example.edu"}),
    version: int = 3,
) -> EmailDomainPolicy:
    return EmailDomainPolicy(mode=mode, allow=allow, version=version, rollout_state=state)


def _reader(
    *,
    stored: EmailDomainPolicy | None = None,
    mirrored: MirroredPolicy | None = None,
    legacy: EmailDomainPolicy | None = None,
) -> tuple[EmailDomainPolicyReader, AsyncMock, AsyncMock, AsyncMock]:
    repository, mirror, legacy_store = AsyncMock(), AsyncMock(), AsyncMock()
    repository.get.return_value = stored
    mirror.read.return_value = mirrored
    legacy_store.read_snapshot.return_value = legacy
    return (
        EmailDomainPolicyReader(repository=repository, mirror=mirror, legacy=legacy_store),
        repository,
        mirror,
        legacy_store,
    )


# ---------------------------------------------------------------------------
# AC-8 - each phase resolves its own authority
# ---------------------------------------------------------------------------


async def test_active_serves_the_resolved_value_itself() -> None:
    stored = _policy(state=_ACTIVE, allow=frozenset({"example.edu"}))
    reader, _, _, legacy = _reader(stored=stored)

    assert await reader.effective_policy() == stored
    # The one phase that answers both questions from one value.
    legacy.read_snapshot.assert_not_awaited()


async def test_compatibility_enforces_the_legacy_triple_not_the_imported_row() -> None:
    """The imported row's lists describe what was captured; old replicas are
    enforcing the live triple, so the new reader must agree with them."""
    imported = _policy(state=_COMPAT, allow=frozenset({"stale.edu"}))
    live = EmailDomainPolicy(
        mode=EmailDomainPolicyMode.ALLOW,
        allow=frozenset({"current.edu"}),
        rollout_state=_COMPAT,
    )
    reader, _, _, legacy = _reader(stored=imported, legacy=live)

    effective = await reader.effective_policy()

    assert effective.allow == frozenset({"current.edu"})
    legacy.read_snapshot.assert_awaited_once()


async def test_rollback_frozen_reads_the_database_rather_than_the_mirror() -> None:
    """A cached snapshot must not be served while the rollback rewrites Redis
    beside it — the two would disagree for the length of a TTL."""
    frozen = _policy(state=_FROZEN, allow=frozenset({"frozen.edu"}))
    reader, repository, _, _ = _reader(
        stored=frozen,
        mirrored=MirroredPolicy(
            policy=_policy(state=_FROZEN, allow=frozenset({"cached.edu"})), ttl_seconds=25
        ),
    )

    effective = await reader.effective_policy()

    assert effective.allow == frozenset({"frozen.edu"})
    repository.get.assert_awaited()


# ---------------------------------------------------------------------------
# AC-11 / AC-12 - cache contract
# ---------------------------------------------------------------------------


async def test_a_mirror_hit_answers_without_touching_the_database() -> None:
    reader, repository, mirror, _ = _reader(mirrored=MirroredPolicy(policy=_policy(), ttl_seconds=20))

    await reader.effective_policy()

    repository.get.assert_not_awaited()
    # A mirror-derived read must never refresh the mirror: renewing the TTL from
    # the value it bounds would make a stale snapshot immortal.
    mirror.write.assert_not_awaited()


async def test_a_mirror_hit_is_cached_no_longer_than_its_remaining_pttl() -> None:
    """A valid mirror value with 2 s left may not seed a 30 s local cache."""
    reader, _, _, _ = _reader(mirrored=MirroredPolicy(policy=_policy(), ttl_seconds=2.0))

    await reader.effective_policy()

    entry = reader_mod._entry
    assert entry is not None
    remaining = entry.expires_at - time.monotonic()
    assert remaining <= 2.0
    assert remaining < LOCAL_CACHE_TTL_SECONDS


async def test_an_expired_mirror_value_is_not_served() -> None:
    """PTTL <= 0 means no key or no expiry. This key is only ever written with a
    TTL, so a TTL-less value is not one this code wrote."""
    reader, repository, _, _ = _reader(
        stored=_policy(),
        mirrored=MirroredPolicy(policy=_policy(allow=frozenset({"stale.edu"})), ttl_seconds=0),
    )

    effective = await reader.effective_policy()

    assert effective.allow == frozenset({"example.edu"})
    repository.get.assert_awaited()


async def test_an_unreadable_mirror_falls_back_to_the_database() -> None:
    reader, repository, mirror, _ = _reader(stored=_policy())
    mirror.read.side_effect = ConnectionError("redis is down")

    assert (await reader.effective_policy()).version == 3
    repository.get.assert_awaited()


async def test_a_database_read_repairs_the_mirror() -> None:
    reader, _, mirror, _ = _reader(stored=_policy())

    await reader.effective_policy()

    mirror.write.assert_awaited_once()


async def test_a_failed_mirror_write_does_not_fail_or_roll_back_the_read() -> None:
    """AC-4: the committed policy is what the caller gets, whatever the cache
    does. The mirror is an accelerator, never a participant in correctness."""
    stored = _policy(version=7)
    reader, _, mirror, _ = _reader(stored=stored)
    mirror.write.side_effect = ConnectionError("redis is down")

    assert await reader.effective_policy() == stored


async def test_losing_both_stores_is_a_typed_refusal_never_off() -> None:
    """The exact condition the Redis-only predecessor read as `off`, which
    silently reopened registration."""
    reader, repository, mirror, _ = _reader()
    mirror.read.side_effect = ConnectionError("redis is down")
    repository.get.side_effect = ConnectionError("postgres is down")

    with pytest.raises(EmailDomainPolicyUnavailable):
        await reader.is_allowed("user@anything.test")


async def test_a_database_failure_while_frozen_is_the_same_typed_refusal() -> None:
    """The frozen branch reads the authority directly, so it has to go through
    the same wrapper as every other read — otherwise the one phase an operator is
    most likely to be watching is the one that answers with a generic 500."""
    reader, repository, _, _ = _reader(mirrored=MirroredPolicy(policy=_policy(state=_FROZEN), ttl_seconds=20))
    repository.get.side_effect = ConnectionError("postgres is down")

    with pytest.raises(EmailDomainPolicyUnavailable):
        await reader.is_allowed("user@example.edu")


async def test_a_missing_row_is_a_typed_refusal_never_off() -> None:
    reader, _, _, _ = _reader(stored=None)

    with pytest.raises(EmailDomainPolicyUnavailable):
        await reader.is_allowed("user@anything.test")


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        orjson.dumps({"schema": 1, "rollout_state": "active", "version": 1, "mode": "off"}),
        orjson.dumps({"schema": SCHEMA, "version": 1, "mode": "off", "allow": [], "deny": []}),
        # The one that must not be defaulted: a phase this build does not know.
        orjson.dumps(
            {
                "schema": SCHEMA,
                "rollout_state": "some_future_phase",
                "version": 1,
                "mode": "off",
                "allow": [],
                "deny": [],
            }
        ),
        orjson.dumps(
            {
                "schema": SCHEMA,
                "rollout_state": "active",
                "version": 1,
                "mode": "sideways",
                "allow": [],
                "deny": [],
            }
        ),
    ],
)
def test_every_malformed_mirror_value_decodes_to_a_miss(raw: bytes) -> None:
    assert _decode(raw) is None


# ---------------------------------------------------------------------------
# AC-2 - the Q-10 legacy classification matrix
# ---------------------------------------------------------------------------


def _reply(
    *,
    allow_type: str = "none",
    deny_type: str = "none",
    mode_type: str = "none",
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    mode: str | None = None,
) -> list[object]:
    return [allow_type, deny_type, mode_type, allow or [], deny or [], mode]


def test_all_three_keys_absent_imports_explicit_off() -> None:
    """The one absence with an unambiguous reading: nobody ever configured it."""
    policy = classify_legacy_snapshot(_reply())
    assert policy.mode is EmailDomainPolicyMode.OFF
    assert policy.allow == frozenset()
    assert policy.deny == frozenset()


def test_an_empty_allow_set_under_allow_mode_is_a_legal_deny_all() -> None:
    policy = classify_legacy_snapshot(_reply(allow_type="set", mode_type="string", mode="allow"))
    assert policy.mode is EmailDomainPolicyMode.ALLOW
    assert policy.allow == frozenset()
    assert not policy.admits("user@example.edu")


def test_an_empty_deny_set_under_deny_mode_is_a_legal_allow_all() -> None:
    policy = classify_legacy_snapshot(_reply(deny_type="set", mode_type="string", mode="deny"))
    assert policy.mode is EmailDomainPolicyMode.DENY
    assert policy.admits("user@example.edu")


def test_off_mode_may_retain_dormant_lists() -> None:
    policy = classify_legacy_snapshot(
        _reply(
            allow_type="set",
            mode_type="string",
            allow=["example.edu"],
            mode="off",
        )
    )
    assert policy.mode is EmailDomainPolicyMode.OFF
    assert policy.allow == frozenset({"example.edu"})


def test_the_snapshot_normalises_members_and_the_mode() -> None:
    policy = classify_legacy_snapshot(
        _reply(
            allow_type="set",
            mode_type="string",
            allow=["Example.EDU", "example.edu"],
            mode="  ALLOW  ",
        )
    )
    assert policy.mode is EmailDomainPolicyMode.ALLOW
    assert policy.allow == frozenset({"example.edu"})


def test_an_absent_mode_with_a_populated_list_blocks_the_import() -> None:
    """The operator set one half of a policy. Either reading of the other half
    could be the wrong one, and a guess would become authoritative."""
    with pytest.raises(InvalidLegacyEmailDomainPolicy, match=KEY_MODE):
        classify_legacy_snapshot(_reply(allow_type="set", allow=["example.edu"]))


def test_an_invalid_mode_blocks_the_import() -> None:
    with pytest.raises(InvalidLegacyEmailDomainPolicy, match=KEY_MODE):
        classify_legacy_snapshot(_reply(mode_type="string", mode="allow-ish"))


@pytest.mark.parametrize(
    ("reply", "expected_key"),
    [
        (_reply(allow_type="string"), KEY_ALLOW),
        (_reply(deny_type="hash"), KEY_DENY),
        (_reply(mode_type="set"), KEY_MODE),
    ],
)
def test_a_wrong_key_type_blocks_the_import(reply: list[object], expected_key: str) -> None:
    with pytest.raises(InvalidLegacyEmailDomainPolicy, match=expected_key):
        classify_legacy_snapshot(reply)


def test_an_invalid_domain_member_blocks_the_import() -> None:
    with pytest.raises(InvalidLegacyEmailDomainPolicy, match=KEY_ALLOW):
        classify_legacy_snapshot(
            _reply(allow_type="set", mode_type="string", allow=["user@example.edu"], mode="allow")
        )


def test_an_imported_snapshot_never_claims_to_be_a_stored_version() -> None:
    """Version 0 and `compatibility`: `create_from_legacy` assigns version 1, and
    a snapshot mistaken for a stored version would defeat the update guard."""
    policy = classify_legacy_snapshot(_reply())
    assert policy.version == 0
    assert policy.rollout_state is _COMPAT
