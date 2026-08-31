"""The email-domain policy against real PostgreSQL and real Redis (R19a.13).

Covers what `docs/tasks/2026-08-30-identity-onboarding-policy-hardening` says the
unit tier cannot settle, because each claim is a database or a Redis fact:

* AC-1  N concurrent first starts produce exactly one version-1 row, and the
        advisory lock is what makes that true rather than the insert racing;
* AC-2  a rejected legacy shape leaves no row behind, so the next boot retries;
* AC-3  the guarded UPDATE actually rejects a stale version and a fenced phase;
* AC-5  registration, change-email and Admin provisioning enforce the same
        normalised policy through the same reader;
* AC-8  a warm reader observes a transition on its next request, and within the
        mirror TTL when the transition's cache delete fails;
* AC-12 a mirror value's PTTL is real and bounds the process cache;
* AC-14 rollback preparation records its marker only after an exact readback.

The singleton is a schema constraint (`ck_email_domain_policies_singleton`), and
a `CHECK` is invisible to the unit tier's `literal_binds` compilation -- see
`backend/CLAUDE.md`'s "PostgreSQL-specific SQL needs a db-tier test".
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.identity.application import email_domain_rollout as rollout
from contexts.identity.application.admin_service import AdminService
from contexts.identity.application.auth_service import AuthService
from contexts.identity.application.email_domain_bootstrap import import_legacy_policy_if_absent
from contexts.identity.application.email_domain_policy_reader import (
    EmailDomainPolicyReader,
    reset_process_cache,
)
from contexts.identity.application.email_domain_policy_service import EmailDomainPolicyService
from contexts.identity.domain.email_domain_policy import (
    EmailDomainPolicyMode,
    EmailDomainPolicyRolloutState,
)
from contexts.identity.domain.errors import (
    EmailDomainDenied,
    EmailDomainPolicyRolloutFenced,
    EmailDomainPolicyUnavailable,
    EmailDomainPolicyVersionMismatch,
    InvalidLegacyEmailDomainPolicy,
)
from contexts.identity.infrastructure import tables as identity_t
from contexts.identity.infrastructure.email_domain_legacy import (
    KEY_ALLOW,
    KEY_DENY,
    KEY_MODE,
    RedisLegacyEmailDomainPolicyStore,
)
from contexts.identity.infrastructure.email_domain_mirror import (
    KEY as MIRROR_KEY,
)
from contexts.identity.infrastructure.email_domain_mirror import (
    RedisEmailDomainPolicyMirror,
)
from contexts.identity.infrastructure.email_domain_repository import EmailDomainPolicyRepository
from shared_kernel.auth import clients
from shared_kernel.auth.password import PasswordHasher

pytestmark = pytest.mark.db

_ORIGIN = "https://smap.test"
_ACTIVE = EmailDomainPolicyRolloutState.ACTIVE
_COMPAT = EmailDomainPolicyRolloutState.COMPATIBILITY
_FROZEN = EmailDomainPolicyRolloutState.ROLLBACK_FROZEN
_LEGACY_KEYS = (KEY_ALLOW, KEY_DENY, KEY_MODE, MIRROR_KEY)


@pytest.fixture(autouse=True)
def _fresh_redis_client() -> Iterator[None]:
    """Each test gets its own event loop, and the process-wide Redis client binds
    to the loop that created it; reusing it across tests raises "attached to a
    different loop"."""
    clients.reset_for_tests()
    yield
    clients.reset_for_tests()


@pytest.fixture(autouse=True)
async def _clean_stores(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[None]:
    """No row, no legacy keys, no mirror, no process cache.

    Every test here decides its own starting state, and the singleton means one
    left-over row would decide the next test's outcome rather than its fixture.
    """

    async def wipe() -> None:
        reset_process_cache()
        async with sessionmaker() as session:
            await session.execute(identity_t.email_domain_policies.delete())
            await session.commit()
        await clients.get_redis().delete(*_LEGACY_KEYS)

    await wipe()
    yield
    await wipe()


def _reader(db: AsyncSession) -> EmailDomainPolicyReader:
    return EmailDomainPolicyReader(
        repository=EmailDomainPolicyRepository(db),
        mirror=RedisEmailDomainPolicyMirror(),
        legacy=RedisLegacyEmailDomainPolicyStore(),
    )


def _service(db: AsyncSession) -> EmailDomainPolicyService:
    return EmailDomainPolicyService(
        db,
        repository=EmailDomainPolicyRepository(db),
        mirror=RedisEmailDomainPolicyMirror(),
    )


async def _set_legacy(
    *, mode: str | None, allow: list[str] | None = None, deny: list[str] | None = None
) -> None:
    redis = clients.get_redis()
    await redis.delete(*_LEGACY_KEYS)
    if allow:
        await redis.sadd(KEY_ALLOW, *allow)
    if deny:
        await redis.sadd(KEY_DENY, *deny)
    if mode is not None:
        await redis.set(KEY_MODE, mode)


async def _bootstrap(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as db, db.begin():
        await import_legacy_policy_if_absent(
            db,
            repository=EmailDomainPolicyRepository(db),
            legacy=RedisLegacyEmailDomainPolicyStore(),
        )


# ---------------------------------------------------------------------------
# AC-1 / AC-2 - the bootstrap import
# ---------------------------------------------------------------------------


async def test_n_concurrent_first_starts_create_exactly_one_version_1_row(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The advisory lock is what makes this true: without it the losers would
    reach the INSERT and one of them would fail on the primary key rather than
    observing the winner's committed row."""
    await _set_legacy(mode="allow", allow=["example.edu", "Dept.Example.EDU"])

    await asyncio.gather(*(_bootstrap(sessionmaker) for _ in range(6)))

    async with sessionmaker() as db:
        rows = (await db.execute(sa.select(identity_t.email_domain_policies))).all()
    assert len(rows) == 1
    (row,) = rows
    assert row.version == 1
    assert row.rollout_state == "compatibility"
    assert row.mode == "allow"
    # Both spellings of one domain collapse to one normalised entry.
    assert sorted(row.allow_domains) == ["dept.example.edu", "example.edu"]


async def test_a_second_row_is_impossible_even_if_a_writer_tries(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Singleton is a schema constraint, not a convention -- so the advisory lock
    only has to elect a winner rather than be the sole guard."""
    await _set_legacy(mode=None)
    await _bootstrap(sessionmaker)

    second_row = sa.text(
        "INSERT INTO email_domain_policies (id, mode, rollout_state, version) VALUES (2, 'off', 'active', 1)"
    )
    async with sessionmaker() as db:
        with pytest.raises(IntegrityError):
            await db.execute(second_row)


async def test_all_three_legacy_keys_absent_imports_explicit_off(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _set_legacy(mode=None)

    await _bootstrap(sessionmaker)

    async with sessionmaker() as db:
        policy = await EmailDomainPolicyRepository(db).get()
    assert policy is not None
    assert policy.mode is EmailDomainPolicyMode.OFF


@pytest.mark.parametrize(
    ("mode", "allow", "deny"),
    [
        # mode absent while a list holds members
        (None, ["example.edu"], []),
        # an invalid mode
        ("allow-ish", [], []),
        # an invalid member
        ("allow", ["user@example.edu"], []),
    ],
)
async def test_a_rejected_legacy_shape_writes_no_row_and_stays_retryable(
    sessionmaker: async_sessionmaker[AsyncSession],
    mode: str | None,
    allow: list[str],
    deny: list[str],
) -> None:
    """The import runs inside the caller's transaction, so a rejection rolls back
    to no row -- which is what makes the next boot a clean retry rather than a
    partially-imported policy."""
    await _set_legacy(mode=mode, allow=allow, deny=deny)

    with pytest.raises(InvalidLegacyEmailDomainPolicy):
        await _bootstrap(sessionmaker)

    async with sessionmaker() as db:
        assert await EmailDomainPolicyRepository(db).get() is None

    # And the repaired triple imports on the retry.
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    async with sessionmaker() as db:
        assert await EmailDomainPolicyRepository(db).get() is not None


async def test_a_wrong_key_type_blocks_the_import(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A real Redis type error, not a simulated one: calling SMEMBERS on a string
    aborts the script, which is why the Lua reader consults TYPE first."""
    redis = clients.get_redis()
    await redis.delete(*_LEGACY_KEYS)
    await redis.set(KEY_ALLOW, "not-a-set")

    with pytest.raises(InvalidLegacyEmailDomainPolicy, match=KEY_ALLOW):
        await _bootstrap(sessionmaker)


# ---------------------------------------------------------------------------
# AC-3 - the guarded update, against the real constraint
# ---------------------------------------------------------------------------


async def _activate(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as db, db.begin():
        await rollout.activate(
            db,
            repository=EmailDomainPolicyRepository(db),
            legacy=RedisLegacyEmailDomainPolicyStore(),
        )
    reset_process_cache()
    await clients.get_redis().delete(MIRROR_KEY)


async def test_a_put_is_fenced_in_compatibility_and_permitted_once_active(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    actor = await _seed_user(sessionmaker)

    async with sessionmaker() as db, pytest.raises(EmailDomainPolicyRolloutFenced):
        await _service(db).replace(
            expected_version=1,
            mode=EmailDomainPolicyMode.ALLOW,
            allow=["example.edu"],
            deny=[],
            actor_user_id=actor,
            actor_ip=None,
        )

    await _activate(sessionmaker)

    async with sessionmaker() as db:
        updated = await _service(db).replace(
            expected_version=2,
            mode=EmailDomainPolicyMode.ALLOW,
            allow=["Example.EDU"],
            deny=[],
            actor_user_id=actor,
            actor_ip=None,
        )
        await db.commit()
    assert updated.version == 3
    assert updated.allow == frozenset({"example.edu"})


async def test_a_stale_version_matches_no_row_and_is_reported_as_a_conflict(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)
    actor = await _seed_user(sessionmaker)

    async with sessionmaker() as db, pytest.raises(EmailDomainPolicyVersionMismatch):
        await _service(db).replace(
            expected_version=99,
            mode=EmailDomainPolicyMode.OFF,
            allow=[],
            deny=[],
            actor_user_id=actor,
            actor_ip=None,
        )


async def test_an_admin_edit_clears_a_rollback_marker(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A marker vouches for what an old image would enforce. An edit moves the
    policy past the mirrored version, so leaving it would be a false guarantee."""
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)
    actor = await _seed_user(sessionmaker)

    async with sessionmaker() as db:
        await db.execute(sa.text("UPDATE email_domain_policies SET legacy_mirrored_version = version"))
        await db.commit()

    async with sessionmaker() as db:
        updated = await _service(db).replace(
            expected_version=2,
            mode=EmailDomainPolicyMode.DENY,
            allow=[],
            deny=["spam.test"],
            actor_user_id=actor,
            actor_ip=None,
        )
        await db.commit()
    assert updated.legacy_mirrored_version is None


# ---------------------------------------------------------------------------
# AC-5 - one policy, three enforcement points
# ---------------------------------------------------------------------------


async def _seed_user(sessionmaker: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with sessionmaker() as db:
        await db.execute(
            identity_t.users.insert().values(
                id=user_id,
                email=f"actor-{user_id}@test.invalid",
                password_hash="x",  # never authenticated against
                email_verified=True,
                status="active",
            )
        )
        await db.commit()
    return user_id


async def test_registration_change_email_and_provisioning_share_one_policy(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """AC-5: three call sites, one reader, one normalised decision. A policy that
    only one of them honoured would make the other two a documented bypass."""
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)
    actor = await _seed_user(sessionmaker)

    async with sessionmaker() as db:
        await _service(db).replace(
            expected_version=2,
            mode=EmailDomainPolicyMode.ALLOW,
            allow=["example.edu"],
            deny=[],
            actor_user_id=actor,
            actor_ip=None,
        )
        await db.commit()
    reset_process_cache()
    await clients.get_redis().delete(MIRROR_KEY)

    async with sessionmaker() as db:
        auth = AuthService(
            db=db,
            hasher=PasswordHasher(),
            email_sender=AsyncMock(),
            public_origin=_ORIGIN,
            email_domain_policy=_reader(db),
        )
        admin = AdminService(db, email_domain_policy=_reader(db), public_origin=_ORIGIN)

        with pytest.raises(EmailDomainDenied):
            await auth.register(
                email="outsider@elsewhere.test",
                password="Str0ng!Pass#1",
                captcha_token=None,
                remote_ip=None,
            )
        with pytest.raises(EmailDomainDenied):
            # The domain gate precedes the password check, so the wrong password
            # here does not weaken the assertion — it proves the gate is first.
            await auth.change_email(
                user_id=actor,
                new_email="outsider@elsewhere.test",
                password="irrelevant",
                remote_ip=None,
            )
        with pytest.raises(EmailDomainDenied):
            await admin.create_user(
                email="outsider@elsewhere.test",
                display_name=None,
                admin_user_id=actor,
                actor_ip=None,
            )

        # And a listed parent does not admit its subdomain.
        with pytest.raises(EmailDomainDenied):
            await admin.create_user(
                email="someone@dept.example.edu",
                display_name=None,
                admin_user_id=actor,
                actor_ip=None,
            )


# ---------------------------------------------------------------------------
# AC-8 / AC-12 - the cache against a real Redis
# ---------------------------------------------------------------------------


async def test_a_database_read_writes_a_mirror_with_a_real_ttl(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)

    async with sessionmaker() as db:
        await _reader(db).effective_policy()

    ttl = await clients.get_redis().pttl(MIRROR_KEY)
    # Written with an expiry, not left immortal: a TTL-less value would be the
    # exact failure the versioned mirror exists to end.
    assert 0 < ttl <= 30_000


async def test_a_reader_whose_local_snapshot_expired_sees_a_transition_at_once(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """AC-8, as the design actually delivers it (see the dossier's D-3).

    The transition deletes the mirror, so a reader that consults the mirror on
    its next request — one whose own in-process snapshot has expired, or a
    process that has just started — observes the new phase immediately rather
    than after a TTL. A reader still holding a live in-process snapshot cannot:
    a process-local cache hit performs no I/O by construction, so nothing
    external can reach it. That case is the test below.
    """
    await _set_legacy(mode="allow", allow=["legacy.edu"])
    await _bootstrap(sessionmaker)

    async with sessionmaker() as db:
        warm = await _reader(db).effective_policy()
    assert warm.allow == frozenset({"legacy.edu"})

    await _activate(sessionmaker)

    async with sessionmaker() as db:
        after = await _reader(db).effective_policy()
    assert after.rollout_state is _ACTIVE
    assert after.allow == frozenset({"legacy.edu"})


async def test_a_reader_still_holding_a_local_snapshot_is_bounded_by_its_own_ttl(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The honest half of AC-8, pinned so nobody later reads the test above as a
    stronger guarantee than it is.

    A maintenance command runs in its own process and can delete the shared
    mirror, but it cannot reach into a serving process's memory. That reader
    keeps answering from its snapshot until the snapshot expires — which is why
    the local cache is capped at the mirror's TTL and never renewed from a cache
    hit. The bound is the same 30 s either way; only "immediately" differs.
    """
    await _set_legacy(mode="allow", allow=["legacy.edu"])
    await _bootstrap(sessionmaker)

    async with sessionmaker() as db:
        await _reader(db).effective_policy()

    # The transition, exactly as the command runs it — but without the
    # `reset_process_cache()` that only a same-process caller could perform.
    async with sessionmaker() as db, db.begin():
        await rollout.activate(
            db,
            repository=EmailDomainPolicyRepository(db),
            legacy=RedisLegacyEmailDomainPolicyStore(),
        )
    await RedisEmailDomainPolicyMirror().delete()

    async with sessionmaker() as db:
        still_warm = await _reader(db).effective_policy()
    # Still the old phase: correct, and bounded.
    assert still_warm.rollout_state is _COMPAT

    # Once the snapshot is gone, the very next read converges.
    reset_process_cache()
    async with sessionmaker() as db:
        converged = await _reader(db).effective_policy()
    assert converged.rollout_state is _ACTIVE


async def test_a_transition_whose_cache_delete_fails_is_still_bounded_by_the_ttl(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The degraded case: the mirror survives the transition, so a *cold* process
    still reads the stale phase -- but only until the 30 s TTL it was written
    with, because nothing can renew it from a cache hit."""
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)

    async with sessionmaker() as db:
        await _reader(db).effective_policy()
    before = await clients.get_redis().pttl(MIRROR_KEY)

    # The transition itself, without its mirror delete.
    async with sessionmaker() as db, db.begin():
        await rollout.activate(
            db,
            repository=EmailDomainPolicyRepository(db),
            legacy=RedisLegacyEmailDomainPolicyStore(),
        )
    reset_process_cache()

    stale = await RedisEmailDomainPolicyMirror().read()
    assert stale is not None
    assert stale.policy.rollout_state is _COMPAT
    # A second read does not extend it: the TTL is the hard bound.
    after = await clients.get_redis().pttl(MIRROR_KEY)
    assert after <= before


async def test_losing_the_row_beneath_a_cold_process_is_a_typed_refusal(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await clients.get_redis().delete(*_LEGACY_KEYS)
    reset_process_cache()

    async with sessionmaker() as db, pytest.raises(EmailDomainPolicyUnavailable):
        await _reader(db).is_allowed("user@anything.test")


# ---------------------------------------------------------------------------
# AC-14 - rollback preparation
# ---------------------------------------------------------------------------


async def test_rollback_preparation_freezes_mirrors_verifies_and_marks(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)
    actor = await _seed_user(sessionmaker)

    async with sessionmaker() as db:
        await _service(db).replace(
            expected_version=2,
            mode=EmailDomainPolicyMode.ALLOW,
            allow=["example.edu", "dept.example.edu"],
            deny=[],
            actor_user_id=actor,
            actor_ip=None,
        )
        await db.commit()

    async with sessionmaker() as db, db.begin():
        frozen = await rollout.freeze_for_rollback(db, repository=EmailDomainPolicyRepository(db))
    assert frozen.rollout_state is _FROZEN

    legacy = RedisLegacyEmailDomainPolicyStore()
    await rollout.mirror_policy_to_legacy(legacy=legacy, policy=frozen)

    # The old image's own reading of the triple, not our copy of it.
    redis = clients.get_redis()
    assert await redis.get(KEY_MODE) == "allow"
    assert await redis.smembers(KEY_ALLOW) == {"example.edu", "dept.example.edu"}

    async with sessionmaker() as db, db.begin():
        report = await rollout.record_verified_mirror(
            db, repository=EmailDomainPolicyRepository(db), version=frozen.version
        )
    assert report.legacy_mirrored_version == frozen.version


async def test_a_replacement_drops_legacy_members_the_new_policy_no_longer_lists(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """DEL before SADD. A union would leave an old image admitting a domain the
    current policy had removed -- silently widening the control during rollback."""
    await _set_legacy(mode="allow", allow=["stale.edu", "example.edu"])
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)
    actor = await _seed_user(sessionmaker)

    async with sessionmaker() as db:
        narrowed = await _service(db).replace(
            expected_version=2,
            mode=EmailDomainPolicyMode.ALLOW,
            allow=["example.edu"],
            deny=[],
            actor_user_id=actor,
            actor_ip=None,
        )
        await db.commit()

    await rollout.mirror_policy_to_legacy(legacy=RedisLegacyEmailDomainPolicyStore(), policy=narrowed)

    assert await clients.get_redis().smembers(KEY_ALLOW) == {"example.edu"}


async def test_a_freeze_rejects_an_admin_update_that_arrives_after_it(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """AC-14's serialisation claim: freeze-first means the verified snapshot
    cannot immediately become stale."""
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)
    actor = await _seed_user(sessionmaker)

    async with sessionmaker() as db, db.begin():
        frozen = await rollout.freeze_for_rollback(db, repository=EmailDomainPolicyRepository(db))

    async with sessionmaker() as db, pytest.raises(EmailDomainPolicyRolloutFenced):
        await _service(db).replace(
            expected_version=frozen.version,
            mode=EmailDomainPolicyMode.DENY,
            allow=[],
            deny=["spam.test"],
            actor_user_id=actor,
            actor_ip=None,
        )


async def test_cancelling_restores_writes_and_clears_the_marker(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _set_legacy(mode="off")
    await _bootstrap(sessionmaker)
    await _activate(sessionmaker)

    async with sessionmaker() as db, db.begin():
        frozen = await rollout.freeze_for_rollback(db, repository=EmailDomainPolicyRepository(db))
    async with sessionmaker() as db, db.begin():
        await rollout.record_verified_mirror(
            db, repository=EmailDomainPolicyRepository(db), version=frozen.version
        )

    async with sessionmaker() as db, db.begin():
        report = await rollout.cancel_rollback(db, repository=EmailDomainPolicyRepository(db))

    assert report.rollout_state == "active"
    assert report.legacy_mirrored_version is None
