"""Admin onboarding hardening (R6.18).

Covers `docs/tasks/2026-08-30-identity-onboarding-policy-hardening`:

* AC-6 a banned, credential-less account cannot receive activation links, and the
       refusal creates no token, no audit row and no rate-limit entry;
* AC-7 one Admin may create at most 60 accounts per rolling 10 minutes, the 61st
       is a 429 carrying the retry hint, and another Admin's bucket is separate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import admin_users as admin_mod
from contexts.identity.application.admin_service import AdminService
from contexts.identity.application.email_domain_policy_reader import EmailDomainPolicyReader
from contexts.identity.domain.errors import AccountBanned, AdminProvisioningRateLimited
from contexts.identity.domain.models import User, UserStatus
from contexts.identity.interfaces import error_mapping
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

_ORIGIN = "https://smap.test"
_ADMIN = uuid.UUID("55555555-5555-5555-5555-555555555555")
_OTHER_ADMIN = uuid.UUID("77777777-7777-7777-7777-777777777777")
_TARGET = uuid.UUID("66666666-6666-6666-6666-666666666666")
_SVC = "contexts.identity.application.admin_service"


def _user(*, status: UserStatus, password_hash: str | None = None, email_verified: bool = False) -> User:
    return User(
        id=_TARGET,
        email="new@example.com",
        password_hash=password_hash,
        email_verified=email_verified,
        status=status,
        banned_reason="spam" if status is UserStatus.BANNED else None,
        banned_at=datetime(2026, 8, 30, tzinfo=UTC) if status is UserStatus.BANNED else None,
        deleted_at=None,
        last_login_at=None,
        version=1,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def _service(*, target: User | None = None) -> AdminService:
    reader = EmailDomainPolicyReader(repository=AsyncMock(), mirror=AsyncMock(), legacy=AsyncMock())
    service = AdminService(AsyncMock(), email_domain_policy=reader, public_origin=_ORIGIN)  # type: ignore[arg-type]
    users = AsyncMock()
    users.get_by_id.return_value = target
    users.get_active_by_email.return_value = None
    users.insert.return_value = _user(status=UserStatus.PENDING)
    service._users = users
    service._reset = AsyncMock()
    service._reset.issue.return_value = ("reset-token", "reset-hash")
    service._verify = AsyncMock()
    service._verify.issue.return_value = ("verify-token", "verify-hash")
    service._identities = AsyncMock()
    service._identities.list_for_user.return_value = []
    return service


def _patched(*, allowed: bool = True, audit: AsyncMock | None = None):
    """The three collaborators both hardened paths touch."""
    return (
        patch(f"{_SVC}.audit.emit", new=audit or AsyncMock()),
        patch.object(EmailDomainPolicyReader, "is_allowed", new_callable=AsyncMock, return_value=True),
        patch(
            f"{_SVC}.ratelimit.check_raw",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(allowed=allowed, retry_after_seconds=42),
        ),
    )


def _fake_session() -> AsyncMock:
    """A zero-parameter callable on purpose: FastAPI reads a dependency
    override's signature, and handing it `AsyncMock` itself turns that class's
    constructor keywords into query parameters — every request then 422s."""
    return AsyncMock()


def _app(*, is_admin: bool = True) -> FastAPI:
    app = FastAPI()
    error_mapping.register(app)
    app.include_router(admin_mod.router, prefix="/api/admin")
    principal = Principal(user_id=_ADMIN, is_admin=is_admin, email_verified=True)
    app.dependency_overrides[current_context] = lambda: RequestContext(principal=principal)
    app.dependency_overrides[db_session] = _fake_session
    return app


# ---------------------------------------------------------------------------
# AC-6 - a banned account cannot be "activated"
# ---------------------------------------------------------------------------


async def test_a_banned_credential_less_account_is_refused_activation_links() -> None:
    """The shape the "still needs activation" test reads as eligible.

    A banned account has no password and no linked identity, so the credential
    check alone treats it exactly like a freshly provisioned one and mints a
    working set-password link for an account that can never activate.
    """
    service = _service(target=_user(status=UserStatus.BANNED))
    audit = AsyncMock()

    p_audit, p_domain, p_rl = _patched(audit=audit)
    with p_audit, p_domain, p_rl as rl, pytest.raises(AccountBanned):
        await service.issue_activation_links(target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None)

    # The refusal precedes every side effect, so a caller grinding this endpoint
    # against a banned account leaves nothing behind to clean up or to burn the
    # target's outstanding tokens.
    service._reset.issue.assert_not_awaited()
    service._verify.issue.assert_not_awaited()
    service._identities.list_for_user.assert_not_awaited()
    rl.assert_not_awaited()
    audit.assert_not_awaited()


async def test_a_banned_account_that_still_holds_a_password_is_also_refused() -> None:
    """Not merely a narrower spelling of the credential check.

    An account banned after it had set a password reaches the same refusal, so
    the guard is on status rather than on the accident of holding a credential.
    """
    service = _service(target=_user(status=UserStatus.BANNED, password_hash="argon2-hash"))

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl, pytest.raises(AccountBanned):
        await service.issue_activation_links(target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None)


async def test_a_pending_account_is_still_served() -> None:
    """The population the refusal must not sweep up."""
    service = _service(target=_user(status=UserStatus.PENDING))

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl:
        links = await service.issue_activation_links(
            target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None
        )

    assert links.set_password_url.endswith("#token=reset-token")


def test_the_route_reports_a_banned_target_as_a_403_problem() -> None:
    service = _service()
    service.issue_activation_links = AsyncMock(side_effect=AccountBanned("user is banned"))
    with patch.object(admin_mod, "create_admin_service", return_value=service):
        client = TestClient(_app())
        response = client.post(f"/api/admin/users/{_TARGET}/activation-links")

    assert response.status_code == 403
    assert response.json()["type"].endswith("auth/banned")


# ---------------------------------------------------------------------------
# AC-7 - the per-Admin provisioning cap
# ---------------------------------------------------------------------------


async def test_provisioning_checks_a_per_admin_bucket_of_60_per_10_minutes() -> None:
    service = _service()

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl as rl:
        await service.create_user(
            email="new@example.com", display_name=None, admin_user_id=_ADMIN, actor_ip=None
        )

    assert rl.await_args is not None
    assert rl.await_args.kwargs == {
        "key": f"rl:admin-provision:u:{_ADMIN}",
        "window_sec": 600,
        "max_count": 60,
    }


async def test_the_61st_creation_is_refused_before_any_side_effect() -> None:
    service = _service()
    audit = AsyncMock()

    p_audit, p_domain, p_rl = _patched(allowed=False, audit=audit)
    with p_audit, p_domain as policy, p_rl, pytest.raises(AdminProvisioningRateLimited) as exc:
        await service.create_user(
            email="new@example.com", display_name=None, admin_user_id=_ADMIN, actor_ip=None
        )

    assert exc.value.retry_after_seconds == 42
    # Ahead of the policy authority as well as the insert: a refused caller must
    # not be able to drive load onto the email-domain policy lookup.
    policy.assert_not_awaited()
    service._users.insert.assert_not_awaited()
    service._reset.issue.assert_not_awaited()
    audit.assert_not_awaited()


async def test_each_admin_has_an_independent_bucket() -> None:
    """Keyed by the authenticated actor, so one Admin's burst cannot lock the
    rest of the platform's Admins out of provisioning."""
    keys: list[str] = []
    for admin_user_id in (_ADMIN, _OTHER_ADMIN):
        service = _service()
        p_audit, p_domain, p_rl = _patched()
        with p_audit, p_domain, p_rl as rl:
            await service.create_user(
                email="new@example.com",
                display_name=None,
                admin_user_id=admin_user_id,
                actor_ip=None,
            )
        assert rl.await_args is not None
        keys.append(rl.await_args.kwargs["key"])

    assert keys[0] != keys[1]


def test_the_route_reports_the_provisioning_cap_as_a_429_problem() -> None:
    service = _service()
    service.create_user = AsyncMock(side_effect=AdminProvisioningRateLimited(42))
    with patch.object(admin_mod, "create_admin_service", return_value=service):
        client = TestClient(_app())
        response = client.post("/api/admin/users", json={"email": "new@example.com"})

    assert response.status_code == 429
    body = response.json()
    assert body["retry_after_seconds"] == 42
    assert body["type"].endswith("admin/provisioning-rate-limited")
