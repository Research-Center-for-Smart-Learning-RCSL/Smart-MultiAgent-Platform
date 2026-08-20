"""Onboarding without SMTP - the admin half (R6.18).

Covers `docs/tasks/2026-08-20-onboarding-without-smtp`:

* AC-5 `POST /api/admin/users` creates a `pending`, unverified, password-less
       account, returns both links, and is refused for a non-admin;
* AC-6 an address the deployment's email-domain policy denies is refused;
* AC-8 `POST /api/admin/users/{id}/activation-links` mints fresh working tokens,
       leaves an already-consumed token alone, and is rate-limited per target;
* AC-9 no token reaches an audit row, a log line, or an error body.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from app.api.v1 import admin_users as admin_mod
from contexts.identity.application.admin_service import AccountAlreadyActivatedError, AdminService
from contexts.identity.domain.errors import ActivationLinkRateLimited, EmailDomainDenied
from contexts.identity.domain.models import User, UserStatus
from contexts.identity.interfaces import error_mapping
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

_ORIGIN = "https://smap.test"
_ADMIN = uuid.UUID("55555555-5555-5555-5555-555555555555")
_TARGET = uuid.UUID("66666666-6666-6666-6666-666666666666")
_RESET_TOKEN = "reset-token-plaintext"
_VERIFY_TOKEN = "verify-token-plaintext"
_SVC = "contexts.identity.application.admin_service"


def _user(
    *,
    status: UserStatus = UserStatus.PENDING,
    email_verified: bool = False,
    password_hash: str | None = None,
) -> User:
    return User(
        id=_TARGET,
        email="new@example.com",
        password_hash=password_hash,
        email_verified=email_verified,
        status=status,
        banned_reason=None,
        banned_at=None,
        deleted_at=None,
        last_login_at=None,
        version=1,
        created_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def _service(db: object) -> AdminService:
    return AdminService(db, public_origin=_ORIGIN)  # type: ignore[arg-type]


def _fake_session() -> AsyncMock:
    """A zero-parameter callable on purpose: FastAPI reads a dependency
    override's signature, and handing it `AsyncMock` itself turns that class's
    constructor keywords into query parameters — every request then 422s."""
    return AsyncMock()


def _app(*, is_admin: bool) -> FastAPI:
    app = FastAPI()
    error_mapping.register(app)
    app.include_router(admin_mod.router, prefix="/api/admin")
    principal = Principal(user_id=_ADMIN, is_admin=is_admin, email_verified=True)
    app.dependency_overrides[current_context] = lambda: RequestContext(principal=principal)
    app.dependency_overrides[db_session] = _fake_session
    return app


def _patched(
    *,
    domain_allowed: bool = True,
    rate_limit_allowed: bool = True,
    audit: AsyncMock | None = None,
):
    """The three collaborators every provisioning path touches."""
    return (
        patch(f"{_SVC}.audit.emit", new=audit or AsyncMock()),
        patch(
            f"{_SVC}.email_domain_policy.is_allowed",
            new_callable=AsyncMock,
            return_value=domain_allowed,
        ),
        patch(
            f"{_SVC}.ratelimit.check_raw",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(allowed=rate_limit_allowed, retry_after_seconds=42),
        ),
    )


def _with_token_repos(service: AdminService, *, identities: list[object] | None = None) -> AdminService:
    service._reset = AsyncMock()
    service._reset.issue.return_value = (_RESET_TOKEN, "reset-hash")
    service._verify = AsyncMock()
    service._verify.issue.return_value = (_VERIFY_TOKEN, "verify-hash")
    service._identities = AsyncMock()
    service._identities.list_for_user.return_value = identities or []
    return service


# ---------------------------------------------------------------------------
# AC-5 - provisioning
# ---------------------------------------------------------------------------


async def test_create_user_provisions_a_pending_unverified_passwordless_account() -> None:
    users = AsyncMock()
    users.get_active_by_email.return_value = None
    users.insert.return_value = _user()
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl:
        user, links = await service.create_user(
            email="  New@Example.com ",
            display_name="Ada",
            admin_user_id=_ADMIN,
            actor_ip="1.2.3.4",
        )

    assert user.status is UserStatus.PENDING
    users.insert.assert_awaited_once_with(
        email="new@example.com",  # normalised exactly as self-registration does
        password_hash=None,
        status=UserStatus.PENDING,
        display_name="Ada",
    )
    assert links.set_password_url == f"{_ORIGIN}/password-reset/confirm#token={_RESET_TOKEN}"
    assert links.verify_email_url == f"{_ORIGIN}/verify-email#token={_VERIFY_TOKEN}"


def test_create_user_route_is_refused_for_a_non_admin() -> None:
    service = _with_token_repos(_service(AsyncMock()))
    service._users = AsyncMock()
    with patch.object(admin_mod, "AdminService", return_value=service):
        client = TestClient(_app(is_admin=False))
        response = client.post("/api/admin/users", json={"email": "new@example.com"})

    assert response.status_code == 403
    service._users.insert.assert_not_awaited()


def test_create_user_route_returns_the_user_and_both_labelled_links() -> None:
    users = AsyncMock()
    users.get_active_by_email.return_value = None
    users.insert.return_value = _user()
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl, patch.object(admin_mod, "AdminService", return_value=service):
        client = TestClient(_app(is_admin=True))
        response = client.post("/api/admin/users", json={"email": "new@example.com", "display_name": "Ada"})

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["status"] == "pending"
    assert body["user"]["email_verified"] is False
    assert body["activation_links"]["set_password_url"].endswith(f"#token={_RESET_TOKEN}")
    assert body["activation_links"]["verify_email_url"].endswith(f"#token={_VERIFY_TOKEN}")
    # Both expiries are surfaced: handing over the wrong link, or a dead one, is
    # the obvious failure mode of an out-of-band handover.
    assert body["activation_links"]["set_password_expires_at"]
    assert body["activation_links"]["verify_email_expires_at"]


async def test_create_user_refuses_an_address_that_already_has_a_live_account() -> None:
    from contexts.identity.domain.errors import EmailAlreadyRegistered

    users = AsyncMock()
    users.get_active_by_email.return_value = _user()
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl, pytest.raises(EmailAlreadyRegistered):
        await service.create_user(
            email="new@example.com",
            display_name=None,
            admin_user_id=_ADMIN,
            actor_ip=None,
        )
    users.insert.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC-6 - the domain policy is not bypassed
# ---------------------------------------------------------------------------


async def test_admin_provisioning_obeys_the_email_domain_policy() -> None:
    """Skipping R19a.13 here would make this endpoint a bypass of a control the
    operator deliberately set."""
    users = AsyncMock()
    users.get_active_by_email.return_value = None
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched(domain_allowed=False)
    with p_audit, p_domain, p_rl, pytest.raises(EmailDomainDenied):
        await service.create_user(
            email="outsider@elsewhere.test",
            display_name=None,
            admin_user_id=_ADMIN,
            actor_ip=None,
        )

    users.insert.assert_not_awaited()
    service._reset.issue.assert_not_awaited()
    service._verify.issue.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC-8 - re-issue
# ---------------------------------------------------------------------------


async def test_reissue_mints_a_fresh_pair_for_an_account_still_needing_activation() -> None:
    users = AsyncMock()
    users.get_by_id.return_value = _user()
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl:
        links = await service.issue_activation_links(
            target_user_id=_TARGET,
            admin_user_id=_ADMIN,
            actor_ip=None,
        )

    assert links.set_password_url.endswith(f"#token={_RESET_TOKEN}")
    assert links.verify_email_url.endswith(f"#token={_VERIFY_TOKEN}")
    # A re-issue burns the earlier *unused* token of each kind (SEC-L2) and
    # touches no consumed one - `issue` filters on `used_at IS NULL`.
    service._reset.issue.assert_awaited_once()
    service._verify.issue.assert_awaited_once()


@pytest.mark.parametrize(
    ("password_hash", "verified"),
    [(None, False), (None, True), ("argon2-hash", False)],
)
async def test_reissue_is_allowed_while_either_activation_step_is_outstanding(
    password_hash: str | None, verified: bool
) -> None:
    users = AsyncMock()
    users.get_by_id.return_value = _user(password_hash=password_hash, email_verified=verified)
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl:
        assert await service.issue_activation_links(
            target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None
        )


async def test_reissue_refuses_a_fully_activated_account() -> None:
    """A set-password link for a live account is a persistent takeover primitive;
    an Admin who must act as a user has impersonation, which is bounded."""
    users = AsyncMock()
    users.get_by_id.return_value = _user(
        status=UserStatus.ACTIVE, email_verified=True, password_hash="argon2-hash"
    )
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl, pytest.raises(AccountAlreadyActivatedError):
        await service.issue_activation_links(target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None)
    service._reset.issue.assert_not_awaited()


async def test_reissue_refuses_a_live_google_only_account() -> None:
    """The account shape a password-only guard reads as "not yet activated".

    A Google-provisioned account is created `password_hash=None`,
    `status=ACTIVE`, `email_verified=True` (`auth_service.py:529-535`), and R6.16
    neutralises the password of an account Google links to. Testing only for a
    password hash therefore hands an Admin a working set-password link for a
    fully live account — a persistent takeover of a user who has never had a
    password, bypassing the bounded, separately audited impersonation path.
    """
    users = AsyncMock()
    users.get_by_id.return_value = _user(status=UserStatus.ACTIVE, email_verified=True, password_hash=None)
    service = _with_token_repos(
        _service(AsyncMock()),
        identities=[SimpleNamespace(provider="google", provider_subject="sub-1")],
    )
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl, pytest.raises(AccountAlreadyActivatedError):
        await service.issue_activation_links(target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None)
    # It must also not have burned the target's outstanding reset token on the
    # way to refusing.
    service._reset.issue.assert_not_awaited()
    service._verify.issue.assert_not_awaited()


async def test_reissue_still_serves_a_provisioned_account_that_verified_first() -> None:
    """The case the credential test must not sweep up: walking the verify link
    before the set-password link promotes the account to ACTIVE and verified, but
    it holds no credential at all and still needs the second link."""
    users = AsyncMock()
    users.get_by_id.return_value = _user(status=UserStatus.ACTIVE, email_verified=True, password_hash=None)
    service = _with_token_repos(_service(AsyncMock()), identities=[])
    service._users = users

    p_audit, p_domain, p_rl = _patched()
    with p_audit, p_domain, p_rl:
        assert await service.issue_activation_links(
            target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None
        )


def test_reissue_route_maps_an_activated_account_to_409_and_a_missing_one_to_404() -> None:
    """The split is on the exception type, not on wording somebody may reword."""
    for error, expected in (
        (AccountAlreadyActivatedError("already activated"), 409),
        (ValueError("user 123 not found"), 404),
    ):
        service = _with_token_repos(_service(AsyncMock()))
        service.issue_activation_links = AsyncMock(side_effect=error)
        with patch.object(admin_mod, "AdminService", return_value=service):
            client = TestClient(_app(is_admin=True))
            response = client.post(f"/api/admin/users/{_TARGET}/activation-links")
        assert response.status_code == expected


async def test_reissue_is_rate_limited_per_target_user() -> None:
    users = AsyncMock()
    users.get_by_id.return_value = _user()
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users

    p_audit, p_domain, p_rl = _patched(rate_limit_allowed=False)
    with p_audit, p_domain, p_rl as rl, pytest.raises(ActivationLinkRateLimited):
        await service.issue_activation_links(target_user_id=_TARGET, admin_user_id=_ADMIN, actor_ip=None)

    assert rl.await_args.kwargs["key"] == f"rl:actlink:u:{_TARGET}"
    service._reset.issue.assert_not_awaited()


def test_reissue_route_reports_the_rate_limit_as_a_429_problem() -> None:
    service = _with_token_repos(_service(AsyncMock()))
    service.issue_activation_links = AsyncMock(side_effect=ActivationLinkRateLimited(42))
    with patch.object(admin_mod, "AdminService", return_value=service):
        client = TestClient(_app(is_admin=True))
        response = client.post(f"/api/admin/users/{_TARGET}/activation-links")

    assert response.status_code == 429
    assert response.json()["retry_after_seconds"] == 42


def test_reissue_route_is_refused_for_a_non_admin() -> None:
    service = _with_token_repos(_service(AsyncMock()))
    service.issue_activation_links = AsyncMock()
    with patch.object(admin_mod, "AdminService", return_value=service):
        client = TestClient(_app(is_admin=False))
        response = client.post(f"/api/admin/users/{_TARGET}/activation-links")

    assert response.status_code == 403
    service.issue_activation_links.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC-9 - no token in an audit row, a log line, or an error body
# ---------------------------------------------------------------------------


async def test_no_plaintext_token_or_address_reaches_the_audit_log_or_logs() -> None:
    users = AsyncMock()
    users.get_active_by_email.return_value = None
    users.insert.return_value = _user()
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users
    audit = AsyncMock()

    emitted: list[str] = []
    sink_id = logger.add(emitted.append, level="TRACE")
    try:
        p_audit, p_domain, p_rl = _patched(audit=audit)
        with p_audit, p_domain, p_rl:
            await service.create_user(
                email="new@example.com",
                display_name=None,
                admin_user_id=_ADMIN,
                actor_ip=None,
            )
    finally:
        logger.remove(sink_id)

    events = [call.args[1] for call in audit.await_args_list]
    assert {e.action for e in events} == {"user.created", "admin.user_activation_links_issued"}
    audited = repr([e.metadata for e in events])
    logged = "".join(emitted)
    for secret in (_RESET_TOKEN, _VERIFY_TOKEN, "new@example.com"):
        assert secret not in audited
        assert secret not in logged
    assert "provisioned_by_admin" in audited


async def test_the_activation_link_audit_names_the_admin_not_the_new_account() -> None:
    """The residual cost of Q-3's out-of-band verification is that provisioning
    proves nothing about address ownership, so the trail must name who vouched."""
    users = AsyncMock()
    users.get_active_by_email.return_value = None
    users.insert.return_value = _user()
    service = _with_token_repos(_service(AsyncMock()))
    service._users = users
    audit = AsyncMock()

    p_audit, p_domain, p_rl = _patched(audit=audit)
    with p_audit, p_domain, p_rl:
        await service.create_user(
            email="new@example.com",
            display_name=None,
            admin_user_id=_ADMIN,
            actor_ip="9.9.9.9",
        )

    for call in audit.await_args_list:
        event = call.args[1]
        assert event.actor_user_id == _ADMIN
        assert event.resource_id == _TARGET
        assert event.actor_ip == "9.9.9.9"
