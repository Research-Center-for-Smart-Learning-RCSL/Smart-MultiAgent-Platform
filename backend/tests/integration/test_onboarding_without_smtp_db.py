"""AC-7 — the end-to-end claim of `docs/tasks/2026-08-20-onboarding-without-smtp`.

An Admin provisions an account, hands over the two links, and the holder walks
them in order (set password, then verify). What comes out must be an account that
can log in and accept an invite. Every link the flow needs comes out of a response
body, so nothing here depends on a message being delivered.

This is the one criterion the unit tier cannot settle: it is a claim about
account *state* moving through two single-use token tables, a partial unique
index, and the R6.02 gates, and every one of those is a database fact.

Real Postgres and real Redis. The only faked collaborator is Vault Transit's
asymmetric JWT signing, which belongs to the compose tier
(`tests/integration/test_auth_login_refresh.py` records the same boundary) and
has nothing to do with what this test asserts: `login` still runs its lockout
check, its Argon2id verification, and both status gates for real, so a wrongly
provisioned account fails here exactly as it would in production.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.identity.application.admin_service import AccountAlreadyActivatedError, AdminService
from contexts.identity.application.auth_service import AuthService
from contexts.identity.domain.errors import AccountNotVerified, InvalidCredentials
from contexts.identity.domain.models import UserStatus
from contexts.identity.infrastructure import tables as identity_t
from contexts.tenancy.application.invite_service import InviteService
from contexts.tenancy.domain.models import InviteScope, InviteState, OrgMemberRole
from contexts.tenancy.infrastructure import tables as tenancy_t
from shared_kernel.auth import clients
from shared_kernel.auth.password import PasswordHasher

pytestmark = pytest.mark.db

_ORIGIN = "https://smap.test"
_PASSWORD = "Provisioned-Passw0rd!"


def _token_from(url: str) -> str:
    """Both link shapes carry the token in the URL fragment (SEC-8)."""
    fragment = urlsplit(url).fragment
    assert fragment.startswith("token="), url
    return fragment.removeprefix("token=")


def _fake_signer():
    """Stand in for Vault Transit. `_establish_session` reads only `jti` and
    `remaining_ttl()` off the claims it returns."""

    def sign(**_: object) -> tuple[str, SimpleNamespace]:
        return "fake-access-token", SimpleNamespace(
            jti=uuid.uuid4(),
            remaining_ttl=lambda: timedelta(minutes=15),
        )

    return patch("contexts.identity.application.auth_service.jwt.sign_access_token", sign)


def _auth(db: AsyncSession) -> AuthService:
    return AuthService(
        db=db,
        hasher=PasswordHasher(),
        # Provisioning and both token walks send nothing; this satisfies the
        # constructor rather than standing in for a transport under test.
        email_sender=AsyncMock(),
        public_origin=_ORIGIN,
    )


@pytest.fixture(autouse=True)
def _fresh_redis_client() -> Iterator[None]:
    """Each test gets its own event loop, and the process-wide Redis client holds
    connections bound to the loop that opened them. Drop the singleton around
    every test so the second one does not inherit a closed loop's socket."""
    clients.reset_for_tests()
    yield
    clients.reset_for_tests()


@pytest.fixture
async def inviter(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """(org_id, owner_user_id) — a real Org whose Owner can issue the invite."""
    org_id, owner_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            identity_t.users.insert().values(
                id=owner_id,
                email=f"owner-{owner_id}@test.invalid",
                password_hash="x",  # never authenticated against
                email_verified=True,
                status=UserStatus.ACTIVE.value,
            )
        )
        await session.execute(
            tenancy_t.orgs.insert().values(id=org_id, name="Closed Lab", creator_user_id=owner_id)
        )
        await session.execute(
            tenancy_t.org_members.insert().values(
                org_id=org_id, user_id=owner_id, role="owner", is_original_creator=True
            )
        )
        await session.commit()
    try:
        yield org_id, owner_id
    finally:
        async with sessionmaker() as session:
            await session.execute(sa.text("SET ROLE smap_audit_retention"))
            await session.execute(
                sa.text("DELETE FROM audit_logs WHERE actor_user_id = :uid").bindparams(uid=owner_id)
            )
            await session.execute(sa.text("RESET ROLE"))
            await session.execute(tenancy_t.orgs.delete().where(tenancy_t.orgs.c.id == org_id))
            await session.execute(identity_t.users.delete().where(identity_t.users.c.id == owner_id))
            await session.commit()


async def test_provisioned_account_walks_both_links_then_logs_in_and_accepts_an_invite(
    sessionmaker: async_sessionmaker[AsyncSession],
    inviter: tuple[uuid.UUID, uuid.UUID],
) -> None:
    org_id, owner_id = inviter
    email = f"provisioned-{uuid.uuid4().hex[:12]}@test.invalid"
    emailer = AsyncMock()

    # --- 1. The Admin provisions the account -------------------------------
    async with sessionmaker() as session:
        admin = AdminService(session, public_origin=_ORIGIN)
        user, links = await admin.create_user(
            email=email,
            display_name="Provisioned Person",
            admin_user_id=owner_id,  # any real user row; the actor is audited, not authorised here
            actor_ip="10.0.0.1",
        )
        await session.commit()

    assert user.status is UserStatus.PENDING
    assert user.email_verified is False
    assert user.password_hash is None

    # It cannot log in yet, and not because the password is wrong.
    with _fake_signer():
        async with sessionmaker() as session:
            with pytest.raises(InvalidCredentials):
                await _auth(session).login(
                    email=email, password=_PASSWORD, remote_ip="10.0.0.2", user_agent=None
                )

    # --- 2. Walk the set-password link -------------------------------------
    async with sessionmaker() as session:
        await _auth(session).reset_password(
            token=_token_from(links.set_password_url),
            new_password=_PASSWORD,
            remote_ip="10.0.0.2",
        )
        await session.commit()

    # Still refused: R6.02's verification gate is untouched by this feature.
    with _fake_signer():
        async with sessionmaker() as session:
            with pytest.raises(AccountNotVerified):
                await _auth(session).login(
                    email=email, password=_PASSWORD, remote_ip="10.0.0.2", user_agent=None
                )

    # --- 3. Walk the verify-email link -------------------------------------
    async with sessionmaker() as session:
        verified = await _auth(session).verify_email(
            _token_from(links.verify_email_url), remote_ip="10.0.0.2"
        )
        await session.commit()
    assert verified.email_verified is True
    assert verified.status is UserStatus.ACTIVE

    # --- 4. Log in ----------------------------------------------------------
    with _fake_signer():
        async with sessionmaker() as session:
            outcome = await _auth(session).login(
                email=email, password=_PASSWORD, remote_ip="10.0.0.2", user_agent="pytest"
            )
            await session.commit()
    assert outcome.user.id == user.id

    # --- 5. The Org Owner invites, and hands over the copied link -----------
    async with sessionmaker() as session:
        created = await InviteService(session, email_sender=emailer, public_origin=_ORIGIN).create_org_invite(
            org_id=org_id,
            inviter_user_id=owner_id,
            invitee_email=email,
            role=OrgMemberRole.MEMBER,
            actor_ip="10.0.0.1",
        )
        await session.commit()

    # --- 6. The invitee redeems it ------------------------------------------
    async with sessionmaker() as session:
        accepted = await InviteService(session, email_sender=emailer, public_origin=_ORIGIN).accept_by_token(
            token=_token_from(created.accept_url),
            caller_user_id=user.id,
            actor_ip="10.0.0.2",
        )
        await session.commit()

    assert accepted.state is InviteState.ACCEPTED
    assert accepted.scope_type is InviteScope.ORG

    async with sessionmaker() as session:
        row = (
            await session.execute(
                tenancy_t.org_members.select().where(
                    sa.and_(
                        tenancy_t.org_members.c.org_id == org_id,
                        tenancy_t.org_members.c.user_id == user.id,
                    )
                )
            )
        ).first()
    assert row is not None, "the accept link did not produce a membership row"
    assert row.role == "member"

    await _cleanup_user(sessionmaker, user.id)


async def test_reissued_links_work_and_the_superseded_ones_do_not(
    sessionmaker: async_sessionmaker[AsyncSession],
    inviter: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """AC-8 against the real token tables. `issue` burns the target's earlier
    *unused* token of the same kind (SEC-L2), so exactly one link per purpose is
    ever live — the property that makes re-issuing safe to offer as a button."""
    _, admin_id = inviter
    email = f"reissued-{uuid.uuid4().hex[:12]}@test.invalid"

    async with sessionmaker() as session:
        admin = AdminService(session, public_origin=_ORIGIN)
        user, first = await admin.create_user(
            email=email, display_name=None, admin_user_id=admin_id, actor_ip=None
        )
        second = await admin.issue_activation_links(
            target_user_id=user.id, admin_user_id=admin_id, actor_ip=None
        )
        await session.commit()

    assert first.set_password_url != second.set_password_url

    # The superseded set-password token is dead.
    async with sessionmaker() as session:
        from contexts.identity.domain.errors import TokenInvalid

        with pytest.raises(TokenInvalid):
            await _auth(session).reset_password(
                token=_token_from(first.set_password_url),
                new_password=_PASSWORD,
                remote_ip=None,
            )

    # The freshly minted one works.
    async with sessionmaker() as session:
        await _auth(session).reset_password(
            token=_token_from(second.set_password_url),
            new_password=_PASSWORD,
            remote_ip=None,
        )
        await session.commit()

    # A consumed token stays consumed; a later re-issue never revives it.
    async with sessionmaker() as session:
        admin = AdminService(session, public_origin=_ORIGIN)
        third = await admin.issue_activation_links(
            target_user_id=user.id, admin_user_id=admin_id, actor_ip=None
        )
        await session.commit()
    assert third.set_password_url != second.set_password_url

    async with sessionmaker() as session:
        from contexts.identity.domain.errors import TokenInvalid

        with pytest.raises(TokenInvalid):
            await _auth(session).reset_password(
                token=_token_from(second.set_password_url),
                new_password=_PASSWORD,
                remote_ip=None,
            )

    await _cleanup_user(sessionmaker, user.id)


async def test_reissue_refuses_a_real_google_only_account(
    sessionmaker: async_sessionmaker[AsyncSession],
    inviter: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """A live Google account holds no password hash, so a password-only guard
    reads it as "still needs activation" and hands out a set-password link for a
    fully active user.

    Against real rows because the guard now depends on `auth_identities`: the
    account shape is exactly what `login_with_oauth` writes (R6.15) —
    `password_hash=None`, `status=ACTIVE`, `email_verified=True`, plus one
    identity row — and a mocked repository can assert the branch without proving
    the row is there to find.
    """
    _, admin_id = inviter
    google_user_id = uuid.uuid4()

    async with sessionmaker() as session:
        await session.execute(
            identity_t.users.insert().values(
                id=google_user_id,
                email=f"google-{google_user_id}@test.invalid",
                password_hash=None,
                status=UserStatus.ACTIVE.value,
                email_verified=True,
            )
        )
        await session.execute(
            identity_t.auth_identities.insert().values(
                user_id=google_user_id,
                provider="google",
                provider_subject=f"sub-{google_user_id}",
                email=f"google-{google_user_id}@test.invalid",
            )
        )
        await session.commit()

    try:
        async with sessionmaker() as session:
            admin = AdminService(session, public_origin=_ORIGIN)
            with pytest.raises(AccountAlreadyActivatedError):
                await admin.issue_activation_links(
                    target_user_id=google_user_id, admin_user_id=admin_id, actor_ip=None
                )

        # The refusal must also leave the account's token tables untouched.
        async with sessionmaker() as session:
            live = (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(identity_t.password_reset_tokens)
                    .where(identity_t.password_reset_tokens.c.user_id == google_user_id)
                )
            ).scalar_one()
        assert live == 0
    finally:
        await _cleanup_user(sessionmaker, google_user_id)


async def test_invitable_pool_is_scoped_to_callers_who_are_in_the_parent_org(
    sessionmaker: async_sessionmaker[AsyncSession],
    inviter: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """The disclosure boundary of the picker, against real rows.

    Capability #14 on an org-owned project does not imply membership of the parent
    Org: a user invited straight into the project as its Owner holds #14 and never
    appears in `org_members`. `GET /api/orgs/{id}/members` is closed to them, so
    the picker must be too — otherwise it becomes a new way to read every address
    in the Org, which is exactly what Q-6 promised it would not be.

    Asserted against Postgres rather than compiled SQL: the predicate is a
    correlated EXISTS across an aliased self-join, and a subtly wrong correlation
    would still render plausible-looking SQL.
    """
    org_id, org_owner_id = inviter
    project_id = uuid.uuid4()
    outsider_id = uuid.uuid4()
    org_member_id = uuid.uuid4()

    async with sessionmaker() as session:
        for uid, label in ((outsider_id, "outsider"), (org_member_id, "orgmember")):
            await session.execute(
                identity_t.users.insert().values(
                    id=uid,
                    email=f"{label}-{uid}@test.invalid",
                    password_hash="x",
                    email_verified=True,
                    status=UserStatus.ACTIVE.value,
                )
            )
        # `org_member_id` is in the Org; `outsider_id` deliberately is not.
        await session.execute(
            tenancy_t.org_members.insert().values(
                org_id=org_id, user_id=org_member_id, role="member", is_original_creator=False
            )
        )
        await session.execute(
            tenancy_t.projects.insert().values(
                id=project_id,
                name="Org Project",
                owner_org_id=org_id,
                created_by_user_id=org_owner_id,
            )
        )
        # Both are Project Owners, so both hold capability #14 on this project.
        for uid in (outsider_id, org_member_id):
            await session.execute(
                tenancy_t.project_members.insert().values(project_id=project_id, user_id=uid, role="owner")
            )
        await session.commit()

    try:
        async with sessionmaker() as session:
            service = InviteService(session, email_sender=AsyncMock(), public_origin=_ORIGIN)

            outsider_pool = await service.invitable_org_members(project_id, caller_user_id=outsider_id)
            member_pool = await service.invitable_org_members(project_id, caller_user_id=org_member_id)
            admin_pool = await service.invitable_org_members(
                project_id, caller_user_id=outsider_id, caller_is_admin=True
            )

        assert outsider_pool == [], "a non-member of the Org must not read its member list"
        # The Org Owner is the only member not already in the project.
        assert [m.user_id for m in member_pool] == [org_owner_id]
        assert [m.user_id for m in admin_pool] == [org_owner_id]
    finally:
        async with sessionmaker() as session:
            await session.execute(tenancy_t.projects.delete().where(tenancy_t.projects.c.id == project_id))
            await session.execute(
                identity_t.users.delete().where(identity_t.users.c.id.in_([outsider_id, org_member_id]))
            )
            await session.commit()


async def _cleanup_user(sessionmaker: async_sessionmaker[AsyncSession], user_id: uuid.UUID) -> None:
    """`audit_logs` FKs to `users` with ON DELETE SET NULL and carries an
    append-only trigger that refuses the UPDATE the cascade performs, so the rows
    have to go first under the retention role — the same deliberate NOINHERIT
    bypass `tests/integration/conftest.py` documents."""
    async with sessionmaker() as session:
        await session.execute(sa.text("SET ROLE smap_audit_retention"))
        await session.execute(
            sa.text("DELETE FROM audit_logs WHERE actor_user_id = :uid").bindparams(uid=user_id)
        )
        await session.execute(sa.text("RESET ROLE"))
        await session.execute(identity_t.users.delete().where(identity_t.users.c.id == user_id))
        await session.commit()
