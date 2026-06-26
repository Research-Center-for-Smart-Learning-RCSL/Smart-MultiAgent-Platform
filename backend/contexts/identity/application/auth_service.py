"""Auth orchestration (register → verify → login → refresh → logout …).

This is where the individual infrastructure pieces are stitched into the
use-cases §22.1 exposes. Routers (app.api) call these methods; nothing else
crosses the HTTP boundary.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.identity.application.auth_email_service import AuthEmailService
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
    OriginalCreatorSelfDeleteBlocked,
    PasswordPolicyViolation,
    TokenExpired,
    TokenInvalid,
)
from contexts.identity.domain.models import Session, User, UserStatus
from contexts.identity.infrastructure import email_domain_policy, lockouts
from contexts.identity.infrastructure.email import EmailSender, recipient_digest
from contexts.identity.infrastructure.repositories import (
    AdminRepository,
    EmailVerifyTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    UserRepository,
)
from shared_kernel import audit
from shared_kernel.auth import captcha, jwt, ratelimit, tokens
from shared_kernel.auth.clients import now
from shared_kernel.auth.password import (
    DUMMY_HASH,
    PasswordHasher,
    PasswordPolicyError,
    validate_password,
)

_VERIFY_TTL = timedelta(hours=24)
_RESET_TTL = timedelta(minutes=30)  # R6.05
_MAX_DISPLAY_NAME = 50


# ---------------------------------------------------------------------------
# Result DTOs — crossed back to the router layer.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: User
    is_admin: bool


@dataclass(frozen=True, slots=True)
class LoginOutcome:
    user: User
    tokens: TokenPair
    session_id: uuid.UUID


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AuthService:
    """Primary use-case entry point for `/api/auth/*`."""

    def __init__(
        self,
        *,
        db: AsyncSession,
        hasher: PasswordHasher,
        email_sender: EmailSender,
        public_origin: str,
    ) -> None:
        self._db = db
        self._hasher = hasher
        self._emailer = email_sender
        self._users = UserRepository(db)
        self._sessions = SessionRepository(db)
        self._verify = EmailVerifyTokenRepository(db)
        self._reset = PasswordResetTokenRepository(db)
        self._admins = AdminRepository(db)
        self._public_origin = public_origin.rstrip("/")
        self._notifier = AuthEmailService(
            db=db,
            email_sender=email_sender,
            public_origin=public_origin,
        )

    # ----- register / verify -----------------------------------------------

    async def register(
        self,
        *,
        email: str,
        password: str,
        captcha_token: str | None,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        email = _normalise_email(email)
        try:
            await captcha.verify(captcha_token, remote_ip=remote_ip)
        except captcha.CaptchaError as exc:
            raise CaptchaRequired(str(exc)) from exc

        if not await email_domain_policy.is_allowed(email):
            raise EmailDomainDenied(f"domain not allowed: {email!r}")

        try:
            validate_password(password)
        except PasswordPolicyError as exc:
            raise PasswordPolicyViolation(exc.detail) from exc

        existing = await self._users.get_active_by_email(email)
        if existing is not None:
            # Anti-enumeration (SEC-M4): the unauthenticated caller must not be
            # able to tell "email taken" from "email new" via the HTTP status.
            # Both branches return 202; the real account holder is told they
            # already have an account out-of-band, over the address they own.
            #
            # Per-email rate limit (mirrors request_password_reset): without it
            # this branch is an unauthenticated mailbomb — anyone could spam a
            # victim's inbox by re-POSTing /register with their address. Cap at
            # 5 / 10 min; over the limit we silently skip the email but still
            # return the same uniform 202.
            notice_key = "rl:reg:e:" + hashlib.sha256(email.encode()).hexdigest()[:24]
            rl = await ratelimit.check_raw(key=notice_key, window_sec=600, max_count=5)
            if rl.allowed:
                await self._notifier.send_already_registered_notice(email, user_id=existing.id)
                await audit.emit(
                    self._db,
                    audit.AuditEvent(
                        action="auth.register.existing_email",
                        actor_user_id=existing.id,
                        actor_ip=remote_ip,
                        resource_type="user",
                        resource_id=existing.id,
                        metadata={"email_digest": recipient_digest(email)},
                        request_id=request_id,
                    ),
                )
            return

        user = await self._users.insert(
            email=email,
            password_hash=self._hasher.hash(password),
            status=UserStatus.PENDING,
        )
        token, _ = await self._verify.issue(user.id, _VERIFY_TTL)
        await self._notifier.send_email_verification(email, token, user_id=user.id)

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="user.created",
                actor_user_id=user.id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user.id,
                metadata={"email_digest": recipient_digest(email)},
                request_id=request_id,
            ),
        )

    async def verify_email(
        self, token: str, *, remote_ip: str | None, request_id: uuid.UUID | None = None
    ) -> User:
        consumed = await self._verify.consume(token)
        if consumed is None:
            raise TokenInvalid("email verification token invalid or expired")
        _, user_id = consumed
        promoted = await self._users.mark_verified(user_id)
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise RuntimeError(f"user {user_id} vanished after mark_verified")
        if not promoted:
            # User was banned or deleted between token issue and consumption.
            if user.status is UserStatus.BANNED:
                raise AccountBanned(user.banned_reason or "banned")
            raise TokenInvalid("verification token no longer applicable")
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.email_verified",
                actor_user_id=user_id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user_id,
                request_id=request_id,
            ),
        )
        return user

    # ----- login / refresh / logout ---------------------------------------

    async def login(
        self,
        *,
        email: str,
        password: str,
        remote_ip: str,
        user_agent: str | None,
        request_id: uuid.UUID | None = None,
    ) -> LoginOutcome:
        email = _normalise_email(email)

        pre = await lockouts.check_only(email, remote_ip)
        if pre.locked:
            raise Lockout(pre.retry_after_seconds)

        user = await self._users.get_active_by_email(email)
        fail = False
        if user is None:
            # Verify the submitted password against a fixed dummy hash so the
            # absence of an account costs the same ~64 MiB/t=3 Argon2id work as
            # a real verify — denying the login timing oracle (SEC-M3).
            self._hasher.verify(DUMMY_HASH, password)
            fail = True
        else:
            # Always verify the password first — regardless of account status —
            # so banned/deleted accounts cost the same Argon2id work as active
            # ones, closing the timing oracle that previously let attackers
            # distinguish banned accounts by response time (SEC-M3).
            verification = self._hasher.verify(user.password_hash, password)
            if not verification.ok:
                fail = True
            elif verification.rehashed and user.status not in (
                UserStatus.DELETED,
                UserStatus.BANNED,
            ):
                # Only write if the hash hasn't changed since we read it —
                # guards against two concurrent logins both attempting the rehash.
                # Skip rehash for deleted/banned accounts (no point upgrading).
                await self._users.set_password(
                    user.id, verification.rehashed, only_if_hash=user.password_hash
                )

        if fail:
            state = await lockouts.check_and_record_failure(email, remote_ip)
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="auth.login.failed",
                    actor_user_id=user.id if user else None,
                    actor_ip=remote_ip,
                    resource_type="user",
                    resource_id=user.id if user else None,
                    metadata={"email_digest": recipient_digest(email)},
                    request_id=request_id,
                ),
            )
            if state.locked:
                raise Lockout(state.retry_after_seconds)
            raise InvalidCredentials()

        if user is None:
            raise RuntimeError("invariant violated: user is None after credential check passed")

        # Status gates fire AFTER password verification — a wrong password
        # always returns InvalidCredentials (with lockout accounting) regardless
        # of account status, and the Argon2id work has already been performed,
        # so no timing oracle leaks whether an account is banned vs non-existent.
        if user.status is UserStatus.DELETED:
            raise AccountDeleted()
        if user.status is UserStatus.BANNED:
            raise AccountBanned(user.banned_reason or "banned")
        await lockouts.clear_account(email)

        if not user.email_verified:
            raise AccountNotVerified()

        is_admin = await self._admins.is_admin(user.id)
        session_id = uuid.uuid4()
        access_token, claims = jwt.sign_access_token(
            user_id=user.id,
            session_id=session_id,
            is_admin=is_admin,
            role="admin" if is_admin else "user",
        )
        refresh_token, record = await tokens.create_session(
            user_id=user.id,
            session_id=session_id,
            last_jti=claims.jti,
        )
        await self._sessions.insert(
            user_id=user.id,
            session_id=session_id,
            family_id=record.family_id,
            refresh_token_hash=tokens.hash_refresh(refresh_token),
            user_agent=user_agent,
            ip_inet=remote_ip,
            last_jti=claims.jti,
            expires_at=record.expires_at,
        )
        await self._users.mark_logged_in(user.id)

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.login.success",
                actor_user_id=user.id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user.id,
                session_id=session_id,
                request_id=request_id,
            ),
        )
        return LoginOutcome(
            user=user,
            tokens=TokenPair(
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="Bearer",  # noqa: S106 — OAuth2 token-type label, not a credential
                expires_in=int(claims.remaining_ttl().total_seconds()),
            ),
            session_id=session_id,
        )

    async def refresh(
        self, *, refresh_token: str, remote_ip: str, request_id: uuid.UUID | None = None
    ) -> TokenPair:
        # Peek at the session to sign the new JWT *before* rotating — so the
        # jti embedded in the JWT equals the jti the refresh record points at.
        peek = await tokens.get_record(refresh_token)
        if peek is None:
            raise TokenInvalid("refresh token not recognised")

        user = await self._users.get_by_id(peek.user_id)
        if user is None or user.status is not UserStatus.ACTIVE:
            # Kill every Redis session family + DB row so no further refresh
            # succeeds, regardless of whether the user was banned, deleted, or
            # simply never verified. Also denylist any outstanding access jti
            # so the 15-minute TTL window closes immediately.
            await self._invalidate_user_sessions(peek.user_id, reason="inactive")
            raise TokenExpired("session user no longer active")

        is_admin = await self._admins.is_admin(user.id)
        access_token, claims = jwt.sign_access_token(
            user_id=user.id,
            session_id=peek.session_id,
            is_admin=is_admin,
            role="admin" if is_admin else "user",
        )
        try:
            new_token, new_record, old_hash = await tokens.rotate_session(
                refresh_token,
                new_jti=claims.jti,
            )
        except tokens.TokenReuseError as exc:
            # The access token we just signed is technically valid — it was
            # signed by Vault Transit before we knew the refresh was bad.
            # Denylist its jti so even if the client does get hold of it they
            # can't use it.
            await tokens.deny_jti(claims.jti, ttl=claims.remaining_ttl())
            raise TokenInvalid(str(exc)) from exc

        await self._sessions.update_on_rotation(
            old_hash=old_hash,
            new_hash=tokens.hash_refresh(new_token),
            new_jti=claims.jti,
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.refresh",
                actor_user_id=user.id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user.id,
                session_id=new_record.session_id,
                request_id=request_id,
            ),
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=new_token,
            token_type="Bearer",  # noqa: S106 — OAuth2 token-type label, not a credential
            expires_in=int(claims.remaining_ttl().total_seconds()),
        )

    async def logout(
        self,
        *,
        refresh_token: str,
        access_jti: uuid.UUID,
        access_ttl: timedelta,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        record = await tokens.revoke_session(refresh_token)
        # Always denylist the access JTI regardless of refresh-token validity —
        # the caller may still hold a live access token even after the refresh
        # token has been revoked or already used.
        await tokens.deny_jti(access_jti, ttl=access_ttl)
        if record is not None:
            await self._sessions.revoke(session_id=record.session_id)
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="auth.logout",
                    actor_user_id=record.user_id,
                    actor_ip=remote_ip,
                    resource_type="user",
                    resource_id=record.user_id,
                    session_id=record.session_id,
                    request_id=request_id,
                ),
            )

    # ----- password reset -------------------------------------------------

    async def request_password_reset(
        self,
        *,
        email: str,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        email = _normalise_email(email)
        # Per-email rate limit: 5 resets per 10 minutes to prevent inbox flooding.
        # Silently drop (don't 429) so the response never reveals whether the
        # address exists or whether this limit was hit.
        email_key = "rl:reset:e:" + hashlib.sha256(email.encode()).hexdigest()[:24]
        rl = await ratelimit.check_raw(key=email_key, window_sec=600, max_count=5)
        if not rl.allowed:
            return
        user = await self._users.get_active_by_email(email)
        if user is None:
            # Do not leak existence — quietly succeed.
            return
        token, _ = await self._reset.issue(user.id, _RESET_TTL)
        await self._notifier.send_password_reset(email, token, user_id=user.id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.password_reset_requested",
                actor_user_id=user.id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user.id,
                request_id=request_id,
            ),
        )

    async def reset_password(
        self,
        *,
        token: str,
        new_password: str,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        try:
            validate_password(new_password)
        except PasswordPolicyError as exc:
            raise PasswordPolicyViolation(exc.detail) from exc
        consumed = await self._reset.consume(token)
        if consumed is None:
            raise TokenInvalid("password reset token invalid or expired")
        _, user_id = consumed
        await self._users.set_password(user_id, self._hasher.hash(new_password))
        await self._invalidate_user_sessions(user_id, reason="password_reset")
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.password_changed",
                actor_user_id=user_id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user_id,
                metadata={"via": "reset"},
                request_id=request_id,
            ),
        )

    async def change_password(
        self,
        *,
        user_id: uuid.UUID,
        current_password: str,
        new_password: str,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        try:
            validate_password(new_password)
        except PasswordPolicyError as exc:
            raise PasswordPolicyViolation(exc.detail) from exc

        user = await self._users.get_by_id(user_id)
        if user is None:
            raise InvalidCredentials()
        verification = self._hasher.verify(user.password_hash, current_password)
        if not verification.ok:
            raise InvalidCredentials()
        await self._users.set_password(user_id, self._hasher.hash(new_password))
        await self._invalidate_user_sessions(user_id, reason="password_change")
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.password_changed",
                actor_user_id=user_id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user_id,
                metadata={"via": "change"},
                request_id=request_id,
            ),
        )

    async def change_email(
        self,
        *,
        user_id: uuid.UUID,
        new_email: str,
        password: str,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        new_email = _normalise_email(new_email)
        if not await email_domain_policy.is_allowed(new_email):
            raise EmailDomainDenied(f"domain not allowed: {new_email!r}")
        user = await self._users.get_by_id(user_id)
        if user is None or not self._hasher.verify(user.password_hash, password).ok:
            raise InvalidCredentials()
        if await self._users.get_active_by_email(new_email) is not None:
            raise EmailAlreadyRegistered(new_email)
        await self._users.set_email(user_id, new_email)
        token, _ = await self._verify.issue(user_id, _VERIFY_TTL)
        await self._notifier.send_email_change_reverify(new_email, token, user_id=user_id)
        await self._invalidate_user_sessions(user_id, reason="email_change")
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.email_changed",
                actor_user_id=user_id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user_id,
                metadata={"new_email_digest": recipient_digest(new_email)},
                request_id=request_id,
            ),
        )

    async def update_display_name(
        self,
        *,
        user_id: uuid.UUID,
        display_name: str | None,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> str | None:
        """Set (or clear) the caller's optional display name.

        Non-destructive and unauthenticated beyond the live session: a display
        name carries no security weight, so unlike email/password changes this
        does not re-prompt for the password or invalidate sessions. Returns the
        normalised value actually stored so the router can echo it back.
        """
        normalised = _normalise_display_name(display_name)
        await self._users.set_display_name(user_id, normalised)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.display_name_changed",
                actor_user_id=user_id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user_id,
                metadata={"cleared": normalised is None},
                request_id=request_id,
            ),
        )
        return normalised

    async def delete_account(
        self,
        *,
        user_id: uuid.UUID,
        password: str,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """Self-service account deletion (R6.07 / R8.14 / R8.18).

        The account is *soft*-deleted (60-day admin-restore window, R8.13); the
        tenancy footprint is torn down in the same transaction. Re-authenticates
        with the current password even though the caller holds a live session —
        this is a destructive, recovery-gated action, so a hijacked session
        alone must not be enough to trigger it.
        """
        # R6.07: re-authenticate.
        user = await self._users.get_by_id(user_id)
        if user is None or not self._hasher.verify(user.password_hash, password).ok:
            raise InvalidCredentials()

        # Cross-context (tenancy). Lazy import mirrors ProjectService→KeysFacade:
        # it avoids a module-load cycle, and the cross-context independence
        # contract is deferred in pyproject's import-linter, so the call is
        # permitted. Shares this session → the cascade is one transaction.
        from contexts.tenancy.interfaces.facade import TenancyFacade

        tenancy = TenancyFacade(self._db)

        # R8.18: refuse while the user is the Original Creator of any Org that
        # still has other active members — they must transfer OC or delete the
        # Org first. 409 carries the blocking Org IDs (see identity error map).
        blocked = await tenancy.orgs_blocking_self_delete(user_id)
        if blocked:
            raise OriginalCreatorSelfDeleteBlocked([str(org_id) for org_id in blocked])

        # R8.14: soft-delete owned projects, drop memberships, reap solo Orgs.
        counts = await tenancy.cascade_account_deletion(
            user_id=user_id,
            actor_ip=remote_ip,
            request_id=request_id,
        )

        await self._users.soft_delete(user_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="user.self_deleted",
                actor_user_id=user_id,
                actor_ip=remote_ip,
                resource_type="user",
                resource_id=user_id,
                metadata=counts,
                request_id=request_id,
            ),
        )
        # Commit the deletion BEFORE the non-transactional Redis session teardown.
        # The db_session dependency only commits after the handler returns, so
        # doing the Redis invalidation first would destroy the user's sessions
        # even if the trailing commit then failed — leaving a logged-out but
        # *undeleted* account. Committing here makes the delete durable first.
        await self._db.commit()
        # Best-effort: the delete is already durable and the auth middleware
        # rejects DELETED users on every request (and on refresh), so a transient
        # Redis outage here must not fail the committed deletion or block the
        # endpoint's cookie clear. Outstanding sessions are then closed lazily by
        # the next request/refresh, which 401s a non-ACTIVE user.
        try:
            await self._invalidate_user_sessions(user_id, reason="account_deleted")
        except Exception:
            logger.bind(event="self_delete_session_teardown_failed", user_id=str(user_id)).warning(
                "account self-delete committed but session invalidation failed; "
                "sessions will lapse on next request/refresh"
            )
        return counts

    # ----- session management (R6.08) -------------------------------------

    async def list_sessions(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[Session]:
        return await self._sessions.list_for_user(user_id, limit=limit, offset=offset)

    async def revoke_session(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        access_ttl: timedelta,
        remote_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        session = await self._sessions.get_by_id(session_id)
        if session is None or session.user_id != user_id:
            # 404 semantics handled by the router; here we just exit.
            return
        await self._sessions.revoke(session_id=session_id)
        if session.last_jti is not None:
            await tokens.deny_jti(session.last_jti, ttl=access_ttl)
        # Kill the family so any outstanding refresh can't roll forward.
        await tokens.kill_family(session.family_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="auth.session_revoked",
                actor_user_id=user_id,
                actor_ip=remote_ip,
                resource_type="session",
                resource_id=session_id,
                session_id=session_id,
                request_id=request_id,
            ),
        )

    # ----- helpers --------------------------------------------------------

    async def _invalidate_user_sessions(self, user_id: uuid.UUID, *, reason: str) -> None:
        # DB mirror is the index we need to find every Redis family this user
        # owns — kill families first, then mark rows revoked.
        sessions = await self._sessions.list_for_user(user_id, limit=10_000)
        for s in sessions:
            await tokens.kill_family(s.family_id)
            if s.last_jti is not None:
                await tokens.deny_jti(
                    s.last_jti,
                    ttl=timedelta(
                        seconds=get_settings().jwt.access_ttl_seconds,
                    ),
                )
        await self._sessions.revoke_all_for_user(user_id)


def _normalise_email(raw: str) -> str:
    e = raw.strip().lower()
    at = e.rfind("@")
    if at <= 0 or at >= len(e) - 1 or len(e) > 320:
        raise InvalidEmailFormat("invalid email address")
    return e


# Format chars that legitimately appear *inside* an emoji grapheme. Kept so
# multi-codepoint emoji (ZWJ sequences like the family/profession emoji, and
# VS16-presented glyphs) survive normalisation; every other control/format char
# — newlines, tabs, and bidi overrides used for display spoofing — is stripped.
_DISPLAY_NAME_KEEP = ("\u200d", "\ufe0f")  # ZERO WIDTH JOINER, VARIATION SELECTOR-16


def _normalise_display_name(raw: str | None) -> str | None:
    """Trim and strip control/format characters; empty collapses to None.

    Printable Unicode (incl. CJK and emoji) is preserved — this is user content,
    not project UI text. Control/format chars (category ``C*``) are removed so a
    name cannot smuggle newlines or bidi overrides into chat author labels, with
    the emoji joiners in ``_DISPLAY_NAME_KEEP`` exempted. Length is capped
    defensively even though the API boundary also validates it.
    """
    if raw is None:
        return None
    cleaned = "".join(
        ch
        for ch in raw
        if ch in _DISPLAY_NAME_KEEP or ch == " " or not unicodedata.category(ch).startswith("C")
    )
    cleaned = cleaned.strip()[:_MAX_DISPLAY_NAME].strip()
    return cleaned or None


__all__ = [
    "AuthService",
    "AuthenticatedUser",
    "LoginOutcome",
    "TokenPair",
    "now",
]
