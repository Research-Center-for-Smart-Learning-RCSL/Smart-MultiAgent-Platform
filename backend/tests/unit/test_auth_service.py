"""Unit tests for AuthService — register, login, refresh, logout,
password reset/change, email change, account deletion, session management.

All infrastructure (repos, Redis, Vault, email sender) is mocked; tests
exercise the service-layer orchestration logic that stitches them together.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.identity.application.auth_service import AuthService, _normalise_email
from contexts.identity.domain.errors import (
    AccountBanned,
    AccountDeleted,
    AccountNotVerified,
    CaptchaRequired,
    EmailAlreadyRegistered,
    EmailDomainDenied,
    InvalidCredentials,
    InvalidEmailFormat,
    Lockout,
    PasswordPolicyViolation,
    TokenExpired,
    TokenInvalid,
)
from contexts.identity.domain.models import User, UserStatus
from shared_kernel.auth.password import PasswordHasher

_NOW = datetime(2026, 6, 22, 12, 0, 0)
_VALID_PASSWORD = "Str0ng!Pass#1"
_HASHER = PasswordHasher()
_PRECOMPUTED_HASH = _HASHER.hash(_VALID_PASSWORD)


def _make_user(
    *,
    status: UserStatus = UserStatus.ACTIVE,
    email: str = "test@example.com",
    email_verified: bool = True,
    banned_reason: str | None = None,
) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        password_hash=_PRECOMPUTED_HASH,
        email_verified=email_verified,
        status=status,
        banned_reason=banned_reason,
        banned_at=None,
        deleted_at=None,
        last_login_at=None,
        version=1,
        created_at=_NOW,
    )


def _make_service(
    *,
    user_repo: AsyncMock | None = None,
    session_repo: AsyncMock | None = None,
    verify_repo: AsyncMock | None = None,
    reset_repo: AsyncMock | None = None,
    admin_repo: AsyncMock | None = None,
) -> AuthService:
    db = AsyncMock()
    hasher = _HASHER
    email_sender = AsyncMock()
    svc = AuthService(
        db=db,
        hasher=hasher,
        email_sender=email_sender,
        public_origin="https://smap.test",
    )
    if user_repo is not None:
        svc._users = user_repo
    if session_repo is not None:
        svc._sessions = session_repo
    if verify_repo is not None:
        svc._verify = verify_repo
    if reset_repo is not None:
        svc._reset = reset_repo
    if admin_repo is not None:
        svc._admins = admin_repo
    svc._notifier = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# _normalise_email
# ---------------------------------------------------------------------------


class TestNormaliseEmail:
    def test_lowercases_and_strips(self) -> None:
        assert _normalise_email("  FOO@BAR.COM  ") == "foo@bar.com"

    def test_no_at_sign(self) -> None:
        with pytest.raises(InvalidEmailFormat):
            _normalise_email("nope")

    def test_at_at_start(self) -> None:
        with pytest.raises(InvalidEmailFormat):
            _normalise_email("@example.com")

    def test_at_at_end(self) -> None:
        with pytest.raises(InvalidEmailFormat):
            _normalise_email("user@")

    def test_too_long(self) -> None:
        with pytest.raises(InvalidEmailFormat):
            _normalise_email("u" * 316 + "@x.com")


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    @patch("contexts.identity.application.auth_service.captcha.verify", new_callable=AsyncMock)
    @patch(
        "contexts.identity.application.auth_service.email_domain_policy.is_allowed",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_new_user_inserted(self, _audit, _domain, _captcha) -> None:
        users = AsyncMock()
        users.get_active_by_email.return_value = None
        new_user = _make_user(status=UserStatus.PENDING)
        users.insert.return_value = new_user
        verify = AsyncMock()
        verify.issue.return_value = ("token123", MagicMock())
        svc = _make_service(user_repo=users, verify_repo=verify)

        await svc.register(
            email="new@example.com",
            password=_VALID_PASSWORD,
            captcha_token="tok",
            remote_ip="1.2.3.4",
        )

        users.insert.assert_awaited_once()
        verify.issue.assert_awaited_once()
        svc._notifier.send_email_verification.assert_awaited_once()

    @patch("contexts.identity.application.auth_service.captcha.verify", new_callable=AsyncMock)
    async def test_captcha_failure_raises(self, mock_captcha) -> None:
        from shared_kernel.auth.captcha import CaptchaError

        mock_captcha.side_effect = CaptchaError("bad")
        svc = _make_service()

        with pytest.raises(CaptchaRequired):
            await svc.register(
                email="a@b.com",
                password=_VALID_PASSWORD,
                captcha_token="bad",
                remote_ip=None,
            )

    @patch("contexts.identity.application.auth_service.captcha.verify", new_callable=AsyncMock)
    @patch(
        "contexts.identity.application.auth_service.email_domain_policy.is_allowed",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_denied_domain_raises(self, _domain, _captcha) -> None:
        svc = _make_service()
        with pytest.raises(EmailDomainDenied):
            await svc.register(
                email="user@evil.com",
                password=_VALID_PASSWORD,
                captcha_token="tok",
                remote_ip=None,
            )

    @patch("contexts.identity.application.auth_service.captcha.verify", new_callable=AsyncMock)
    @patch(
        "contexts.identity.application.auth_service.email_domain_policy.is_allowed",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_weak_password_raises(self, _domain, _captcha) -> None:
        svc = _make_service()
        with pytest.raises(PasswordPolicyViolation):
            await svc.register(
                email="a@b.com",
                password="short",
                captcha_token="tok",
                remote_ip=None,
            )

    @patch("contexts.identity.application.auth_service.captcha.verify", new_callable=AsyncMock)
    @patch(
        "contexts.identity.application.auth_service.email_domain_policy.is_allowed",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("contexts.identity.application.auth_service.ratelimit.check_raw", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_existing_email_sends_notice_silently(self, _audit, mock_rl, _domain, _captcha) -> None:
        existing = _make_user()
        users = AsyncMock()
        users.get_active_by_email.return_value = existing
        mock_rl.return_value = MagicMock(allowed=True)
        svc = _make_service(user_repo=users)

        await svc.register(
            email=existing.email,
            password=_VALID_PASSWORD,
            captcha_token="tok",
            remote_ip="1.2.3.4",
        )

        users.insert.assert_not_awaited()
        svc._notifier.send_already_registered_notice.assert_awaited_once()


# ---------------------------------------------------------------------------
# verify_email
# ---------------------------------------------------------------------------


class TestVerifyEmail:
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_valid_token_promotes_user(self, _audit) -> None:
        user = _make_user(status=UserStatus.ACTIVE)
        users = AsyncMock()
        users.mark_verified.return_value = True
        users.get_by_id.return_value = user
        verify = AsyncMock()
        verify.consume.return_value = ("token_id", user.id)
        svc = _make_service(user_repo=users, verify_repo=verify)

        result = await svc.verify_email("tok", remote_ip="1.2.3.4")

        assert result.id == user.id
        users.mark_verified.assert_awaited_once_with(user.id)

    async def test_invalid_token_raises(self) -> None:
        verify = AsyncMock()
        verify.consume.return_value = None
        svc = _make_service(verify_repo=verify)

        with pytest.raises(TokenInvalid):
            await svc.verify_email("bad", remote_ip=None)

    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_banned_user_after_verify_raises(self, _audit) -> None:
        user = _make_user(status=UserStatus.BANNED, banned_reason="spam")
        users = AsyncMock()
        users.mark_verified.return_value = False
        users.get_by_id.return_value = user
        verify = AsyncMock()
        verify.consume.return_value = ("tid", user.id)
        svc = _make_service(user_repo=users, verify_repo=verify)

        with pytest.raises(AccountBanned):
            await svc.verify_email("tok", remote_ip=None)


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


class TestLogin:
    @patch("contexts.identity.application.auth_service.lockouts.check_only", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.lockouts.clear_account", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.jwt.sign_access_token")
    @patch("contexts.identity.application.auth_service.tokens.create_session", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_successful_login(self, _audit, mock_session, mock_jwt, _clear, mock_check) -> None:
        mock_check.return_value = MagicMock(locked=False)
        user = _make_user()
        users = AsyncMock()
        users.get_active_by_email.return_value = user
        users.mark_logged_in = AsyncMock()
        users.set_password = AsyncMock()
        admins = AsyncMock()
        admins.is_admin.return_value = False
        sessions = AsyncMock()
        claims = MagicMock()
        claims.jti = uuid.uuid4()
        claims.remaining_ttl.return_value = timedelta(seconds=900)
        mock_jwt.return_value = ("access_tok", claims)
        record = MagicMock()
        record.family_id = uuid.uuid4()
        record.expires_at = _NOW + timedelta(days=7)
        mock_session.return_value = ("refresh_tok", record)
        svc = _make_service(user_repo=users, session_repo=sessions, admin_repo=admins)

        outcome = await svc.login(
            email=user.email,
            password=_VALID_PASSWORD,
            remote_ip="1.2.3.4",
            user_agent="test",
        )

        assert outcome.user.id == user.id
        assert outcome.tokens.access_token == "access_tok"
        assert outcome.tokens.refresh_token == "refresh_tok"
        sessions.insert.assert_awaited_once()

    @patch("contexts.identity.application.auth_service.lockouts.check_only", new_callable=AsyncMock)
    async def test_lockout_raises(self, mock_check) -> None:
        mock_check.return_value = MagicMock(locked=True, retry_after_seconds=300)
        svc = _make_service()

        with pytest.raises(Lockout):
            await svc.login(
                email="a@b.com",
                password=_VALID_PASSWORD,
                remote_ip="1.2.3.4",
                user_agent=None,
            )

    @patch("contexts.identity.application.auth_service.lockouts.check_only", new_callable=AsyncMock)
    @patch(
        "contexts.identity.application.auth_service.lockouts.check_and_record_failure", new_callable=AsyncMock
    )
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_wrong_password_raises(self, _audit, mock_record, mock_check) -> None:
        mock_check.return_value = MagicMock(locked=False)
        mock_record.return_value = MagicMock(locked=False)
        user = _make_user()
        users = AsyncMock()
        users.get_active_by_email.return_value = user
        svc = _make_service(user_repo=users)

        with pytest.raises(InvalidCredentials):
            await svc.login(
                email=user.email,
                password="Wrong!Password0",
                remote_ip="1.2.3.4",
                user_agent=None,
            )

    @patch("contexts.identity.application.auth_service.lockouts.check_only", new_callable=AsyncMock)
    @patch(
        "contexts.identity.application.auth_service.lockouts.check_and_record_failure", new_callable=AsyncMock
    )
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_nonexistent_user_raises(self, _audit, mock_record, mock_check) -> None:
        mock_check.return_value = MagicMock(locked=False)
        mock_record.return_value = MagicMock(locked=False)
        users = AsyncMock()
        users.get_active_by_email.return_value = None
        svc = _make_service(user_repo=users)

        with pytest.raises(InvalidCredentials):
            await svc.login(
                email="nope@example.com",
                password=_VALID_PASSWORD,
                remote_ip="1.2.3.4",
                user_agent=None,
            )

    @patch("contexts.identity.application.auth_service.lockouts.check_only", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.lockouts.clear_account", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_deleted_account_raises(self, _audit, _clear, mock_check) -> None:
        mock_check.return_value = MagicMock(locked=False)
        user = _make_user(status=UserStatus.DELETED)
        users = AsyncMock()
        users.get_active_by_email.return_value = user
        users.set_password = AsyncMock()
        svc = _make_service(user_repo=users)

        with pytest.raises(AccountDeleted):
            await svc.login(
                email=user.email,
                password=_VALID_PASSWORD,
                remote_ip="1.2.3.4",
                user_agent=None,
            )

    @patch("contexts.identity.application.auth_service.lockouts.check_only", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.lockouts.clear_account", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_banned_account_raises(self, _audit, _clear, mock_check) -> None:
        mock_check.return_value = MagicMock(locked=False)
        user = _make_user(status=UserStatus.BANNED, banned_reason="spam")
        users = AsyncMock()
        users.get_active_by_email.return_value = user
        users.set_password = AsyncMock()
        svc = _make_service(user_repo=users)

        with pytest.raises(AccountBanned):
            await svc.login(
                email=user.email,
                password=_VALID_PASSWORD,
                remote_ip="1.2.3.4",
                user_agent=None,
            )

    @patch("contexts.identity.application.auth_service.lockouts.check_only", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.lockouts.clear_account", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_unverified_account_raises(self, _audit, _clear, mock_check) -> None:
        mock_check.return_value = MagicMock(locked=False)
        user = _make_user(email_verified=False)
        users = AsyncMock()
        users.get_active_by_email.return_value = user
        users.set_password = AsyncMock()
        svc = _make_service(user_repo=users)

        with pytest.raises(AccountNotVerified):
            await svc.login(
                email=user.email,
                password=_VALID_PASSWORD,
                remote_ip="1.2.3.4",
                user_agent=None,
            )


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    @patch("contexts.identity.application.auth_service.tokens.get_record", new_callable=AsyncMock)
    async def test_unknown_refresh_token_raises(self, mock_get) -> None:
        mock_get.return_value = None
        svc = _make_service()

        with pytest.raises(TokenInvalid):
            await svc.refresh(refresh_token="bad", remote_ip="1.2.3.4")

    @patch("contexts.identity.application.auth_service.tokens.get_record", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.kill_family", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    async def test_inactive_user_invalidates_sessions(self, _deny, _kill, mock_get) -> None:
        record = MagicMock()
        record.user_id = uuid.uuid4()
        record.session_id = uuid.uuid4()
        mock_get.return_value = record
        users = AsyncMock()
        users.get_by_id.return_value = _make_user(status=UserStatus.BANNED)
        sessions = AsyncMock()
        sessions.list_for_user.return_value = []
        svc = _make_service(user_repo=users, session_repo=sessions)

        with pytest.raises(TokenExpired):
            await svc.refresh(refresh_token="tok", remote_ip="1.2.3.4")

    @patch("contexts.identity.application.auth_service.tokens.get_record", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.rotate_session", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.jwt.sign_access_token")
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_successful_refresh(self, _audit, mock_jwt, mock_rotate, mock_get) -> None:
        user = _make_user()
        record = MagicMock()
        record.user_id = user.id
        record.session_id = uuid.uuid4()
        mock_get.return_value = record
        users = AsyncMock()
        users.get_by_id.return_value = user
        admins = AsyncMock()
        admins.is_admin.return_value = False
        sessions = AsyncMock()
        claims = MagicMock()
        claims.jti = uuid.uuid4()
        claims.remaining_ttl.return_value = timedelta(seconds=900)
        mock_jwt.return_value = ("new_access", claims)
        new_record = MagicMock()
        new_record.session_id = record.session_id
        mock_rotate.return_value = ("new_refresh", new_record, "old_hash")
        svc = _make_service(user_repo=users, session_repo=sessions, admin_repo=admins)

        pair = await svc.refresh(refresh_token="tok", remote_ip="1.2.3.4")

        assert pair.access_token == "new_access"
        assert pair.refresh_token == "new_refresh"
        sessions.update_on_rotation.assert_awaited_once()


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


class TestLogout:
    @patch("contexts.identity.application.auth_service.tokens.revoke_session", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_successful_logout(self, _audit, _deny, mock_revoke) -> None:
        record = MagicMock()
        record.user_id = uuid.uuid4()
        record.session_id = uuid.uuid4()
        mock_revoke.return_value = record
        sessions = AsyncMock()
        svc = _make_service(session_repo=sessions)

        await svc.logout(
            refresh_token="tok",
            access_jti=uuid.uuid4(),
            access_ttl=timedelta(seconds=900),
            remote_ip="1.2.3.4",
        )

        sessions.revoke.assert_awaited_once_with(session_id=record.session_id)

    @patch("contexts.identity.application.auth_service.tokens.revoke_session", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    async def test_logout_with_already_revoked_token(self, _deny, mock_revoke) -> None:
        mock_revoke.return_value = None
        sessions = AsyncMock()
        svc = _make_service(session_repo=sessions)

        await svc.logout(
            refresh_token="expired",
            access_jti=uuid.uuid4(),
            access_ttl=timedelta(seconds=900),
            remote_ip=None,
        )

        sessions.revoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# password reset
# ---------------------------------------------------------------------------


class TestPasswordReset:
    @patch("contexts.identity.application.auth_service.ratelimit.check_raw", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_request_password_reset_sends_email(self, _audit, mock_rl) -> None:
        mock_rl.return_value = MagicMock(allowed=True)
        user = _make_user()
        users = AsyncMock()
        users.get_active_by_email.return_value = user
        reset = AsyncMock()
        reset.issue.return_value = ("reset_tok", MagicMock())
        svc = _make_service(user_repo=users, reset_repo=reset)

        await svc.request_password_reset(email=user.email, remote_ip="1.2.3.4")

        reset.issue.assert_awaited_once()
        svc._notifier.send_password_reset.assert_awaited_once()

    @patch("contexts.identity.application.auth_service.ratelimit.check_raw", new_callable=AsyncMock)
    async def test_request_password_reset_nonexistent_user_silent(self, mock_rl) -> None:
        mock_rl.return_value = MagicMock(allowed=True)
        users = AsyncMock()
        users.get_active_by_email.return_value = None
        svc = _make_service(user_repo=users)

        await svc.request_password_reset(email="nope@example.com", remote_ip=None)

    @patch("contexts.identity.application.auth_service.ratelimit.check_raw", new_callable=AsyncMock)
    async def test_request_password_reset_rate_limited(self, mock_rl) -> None:
        mock_rl.return_value = MagicMock(allowed=False)
        users = AsyncMock()
        svc = _make_service(user_repo=users)

        await svc.request_password_reset(email="a@b.com", remote_ip=None)

        users.get_active_by_email.assert_not_awaited()

    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.kill_family", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    async def test_reset_password_success(self, _deny, _kill, _audit) -> None:
        user_id = uuid.uuid4()
        reset = AsyncMock()
        reset.consume.return_value = ("tid", user_id)
        users = AsyncMock()
        sessions = AsyncMock()
        sessions.list_for_user.return_value = []
        svc = _make_service(user_repo=users, reset_repo=reset, session_repo=sessions)

        await svc.reset_password(token="tok", new_password=_VALID_PASSWORD, remote_ip=None)

        users.set_password.assert_awaited_once()

    async def test_reset_password_invalid_token(self) -> None:
        reset = AsyncMock()
        reset.consume.return_value = None
        svc = _make_service(reset_repo=reset)

        with pytest.raises(TokenInvalid):
            await svc.reset_password(token="bad", new_password=_VALID_PASSWORD, remote_ip=None)

    async def test_reset_password_weak_password(self) -> None:
        svc = _make_service()
        with pytest.raises(PasswordPolicyViolation):
            await svc.reset_password(token="tok", new_password="short", remote_ip=None)


# ---------------------------------------------------------------------------
# change password
# ---------------------------------------------------------------------------


class TestChangePassword:
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.kill_family", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    async def test_success(self, _deny, _kill, _audit) -> None:
        user = _make_user()
        users = AsyncMock()
        users.get_by_id.return_value = user
        sessions = AsyncMock()
        sessions.list_for_user.return_value = []
        svc = _make_service(user_repo=users, session_repo=sessions)

        await svc.change_password(
            user_id=user.id,
            current_password=_VALID_PASSWORD,
            new_password="New!Passw0rd#2",
            remote_ip=None,
        )

        users.set_password.assert_awaited_once()

    async def test_wrong_current_password(self) -> None:
        user = _make_user()
        users = AsyncMock()
        users.get_by_id.return_value = user
        svc = _make_service(user_repo=users)

        with pytest.raises(InvalidCredentials):
            await svc.change_password(
                user_id=user.id,
                current_password="Wrong!Password0",
                new_password="New!Passw0rd#2",
                remote_ip=None,
            )

    async def test_weak_new_password(self) -> None:
        svc = _make_service()
        with pytest.raises(PasswordPolicyViolation):
            await svc.change_password(
                user_id=uuid.uuid4(),
                current_password=_VALID_PASSWORD,
                new_password="weak",
                remote_ip=None,
            )


# ---------------------------------------------------------------------------
# change email
# ---------------------------------------------------------------------------


class TestChangeEmail:
    @patch(
        "contexts.identity.application.auth_service.email_domain_policy.is_allowed",
        new_callable=AsyncMock,
        return_value=True,
    )
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.kill_family", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    async def test_success(self, _deny, _kill, _audit, _domain) -> None:
        user = _make_user()
        users = AsyncMock()
        users.get_by_id.return_value = user
        users.get_active_by_email.return_value = None
        verify = AsyncMock()
        verify.issue.return_value = ("tok", MagicMock())
        sessions = AsyncMock()
        sessions.list_for_user.return_value = []
        svc = _make_service(user_repo=users, verify_repo=verify, session_repo=sessions)

        await svc.change_email(
            user_id=user.id,
            new_email="new@example.com",
            password=_VALID_PASSWORD,
            remote_ip=None,
        )

        users.set_email.assert_awaited_once_with(user.id, "new@example.com")

    @patch(
        "contexts.identity.application.auth_service.email_domain_policy.is_allowed",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_email_already_taken(self, _domain) -> None:
        user = _make_user()
        users = AsyncMock()
        users.get_by_id.return_value = user
        users.get_active_by_email.return_value = _make_user(email="taken@example.com")
        svc = _make_service(user_repo=users)

        with pytest.raises(EmailAlreadyRegistered):
            await svc.change_email(
                user_id=user.id,
                new_email="taken@example.com",
                password=_VALID_PASSWORD,
                remote_ip=None,
            )

    @patch(
        "contexts.identity.application.auth_service.email_domain_policy.is_allowed",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_denied_domain(self, _domain) -> None:
        svc = _make_service()
        with pytest.raises(EmailDomainDenied):
            await svc.change_email(
                user_id=uuid.uuid4(),
                new_email="u@evil.com",
                password=_VALID_PASSWORD,
                remote_ip=None,
            )


# ---------------------------------------------------------------------------
# delete account
# ---------------------------------------------------------------------------


class TestDeleteAccount:
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.kill_family", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    async def test_wrong_password_raises(self, _deny, _kill, _audit) -> None:
        user = _make_user()
        users = AsyncMock()
        users.get_by_id.return_value = user
        svc = _make_service(user_repo=users)

        with pytest.raises(InvalidCredentials):
            await svc.delete_account(
                user_id=user.id,
                password="Wrong!Password0",
                remote_ip=None,
            )

    async def test_nonexistent_user_raises(self) -> None:
        users = AsyncMock()
        users.get_by_id.return_value = None
        svc = _make_service(user_repo=users)

        with pytest.raises(InvalidCredentials):
            await svc.delete_account(
                user_id=uuid.uuid4(),
                password=_VALID_PASSWORD,
                remote_ip=None,
            )


# ---------------------------------------------------------------------------
# session management
# ---------------------------------------------------------------------------


class TestSessionManagement:
    async def test_list_sessions(self) -> None:
        sessions = AsyncMock()
        sessions.list_for_user.return_value = []
        svc = _make_service(session_repo=sessions)

        result = await svc.list_sessions(user_id=uuid.uuid4())

        assert result == []

    @patch("contexts.identity.application.auth_service.tokens.deny_jti", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.tokens.kill_family", new_callable=AsyncMock)
    @patch("contexts.identity.application.auth_service.audit.emit", new_callable=AsyncMock)
    async def test_revoke_session(self, _audit, _kill, _deny) -> None:
        user_id = uuid.uuid4()
        session_id = uuid.uuid4()
        session = MagicMock()
        session.user_id = user_id
        session.last_jti = uuid.uuid4()
        session.family_id = uuid.uuid4()
        sessions = AsyncMock()
        sessions.get_by_id.return_value = session
        svc = _make_service(session_repo=sessions)

        await svc.revoke_session(
            user_id=user_id,
            session_id=session_id,
            access_ttl=timedelta(seconds=900),
            remote_ip=None,
        )

        sessions.revoke.assert_awaited_once()

    async def test_revoke_session_wrong_user(self) -> None:
        session = MagicMock()
        session.user_id = uuid.uuid4()
        sessions = AsyncMock()
        sessions.get_by_id.return_value = session
        svc = _make_service(session_repo=sessions)

        await svc.revoke_session(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            access_ttl=timedelta(seconds=900),
            remote_ip=None,
        )

        sessions.revoke.assert_not_awaited()
