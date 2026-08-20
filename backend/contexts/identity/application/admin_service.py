"""Admin user-management service (I.1).

Orchestrates user ban/unban/delete/restore, admin promote/demote with
last-admin guard, user search, and account provisioning (R6.18). Each write
emits an audit event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.identity.application.auth_email_service import password_reset_url, verify_email_url

# Private-by-convention reuse within one layer of one context: normalisation and
# the two TTLs must be identical to the self-registration path or a provisioned
# account is validated by different rules than a self-registered one (R6.18).
from contexts.identity.application.auth_service import (
    _RESET_TTL,
    _VERIFY_TTL,
    _normalise_display_name,
    _normalise_email,
)
from contexts.identity.domain.errors import (
    ActivationLinkRateLimited,
    EmailAlreadyRegistered,
    EmailDomainDenied,
)
from contexts.identity.domain.models import User, UserStatus
from contexts.identity.infrastructure import email_domain_policy
from contexts.identity.infrastructure import tables as t
from contexts.identity.infrastructure.channels import user_channel
from contexts.identity.infrastructure.email import recipient_digest
from contexts.identity.infrastructure.repositories import (
    AdminRepository,
    AuthIdentityRepository,
    EmailVerifyTokenRepository,
    PasswordResetTokenRepository,
    SessionRepository,
    UserRepository,
    row_to_user,
)
from contexts.notification.interfaces.facade import NotificationFacade, NotificationKind
from shared_kernel import audit
from shared_kernel.auth import ratelimit, tokens
from shared_kernel.auth.clients import now
from shared_kernel.db.restore import raise_restore_conflict
from shared_kernel.realtime.pubsub import Publisher

# Per-target-user cap on activation-link mints (§8). Same shape as the
# per-recipient invite and password-reset caps.
_ACTIVATION_LINK_WINDOW_SEC = 600
_ACTIVATION_LINK_MAX = 5


def _default_public_origin() -> str:
    # Single origin (§19a.07); mirrors app.api.v1.auth._public_origin.
    origins = get_settings().security.cors_origins
    return (origins[0] if origins else "http://localhost:8080").rstrip("/")


@dataclass(frozen=True, slots=True)
class AdminEntry:
    user_id: uuid.UUID
    promoted_by_user_id: uuid.UUID | None
    promoted_at: datetime


@dataclass(frozen=True, slots=True)
class UserDetail:
    user: User
    is_admin: bool
    org_ids: list[uuid.UUID]
    project_ids: list[uuid.UUID]


@dataclass(frozen=True, slots=True)
class ActivationLinks:
    """The two links an Admin hands to a provisioned account holder (R6.18).

    Both are single-use bearer credentials — the set-password one is equivalent
    to the password itself — so they may be returned to the minting Admin and to
    nobody else: never logged, never audited, never carried by a read endpoint.
    """

    set_password_url: str
    verify_email_url: str
    set_password_expires_at: datetime
    verify_email_expires_at: datetime


class LastAdminError(Exception):
    pass


class SelfTargetError(ValueError):
    pass


class AccountAlreadyActivatedError(ValueError):
    """Activation links were requested for an account that no longer needs them.

    A distinct type rather than a message the route pattern-matches: the 404/409
    split must not depend on wording somebody may reasonably reword.
    """


class AdminService:
    def __init__(self, db: AsyncSession, *, public_origin: str | None = None) -> None:
        self._db = db
        self._users = UserRepository(db)
        self._admins = AdminRepository(db)
        self._sessions = SessionRepository(db)
        self._verify = EmailVerifyTokenRepository(db)
        self._reset = PasswordResetTokenRepository(db)
        self._identities = AuthIdentityRepository(db)
        self._public_origin = (public_origin or _default_public_origin()).rstrip("/")

    # ----- provisioning (R6.18) -------------------------------------------

    async def create_user(
        self,
        *,
        email: str,
        display_name: str | None,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> tuple[User, ActivationLinks]:
        """Provision an account for someone who cannot self-register (R6.18).

        The account is created exactly as a self-registered one would be —
        ``pending``, unverified, and with no password — so none of R6.02's gates
        are weakened: it still cannot log in, create an Org, or accept an invite
        until the holder verifies. No membership row is written anywhere; consent
        is preserved (Q-1), and the only thing the Admin gains is two links to
        hand over.

        The email-domain policy of R19a.13 applies here too. Skipping it would
        make this endpoint a bypass of a control the operator deliberately set.
        """
        email = _normalise_email(email)
        if not await email_domain_policy.is_allowed(email):
            raise EmailDomainDenied(f"domain not allowed: {email!r}")
        if await self._users.get_active_by_email(email) is not None:
            raise EmailAlreadyRegistered(email)
        try:
            user = await self._users.insert(
                email=email,
                password_hash=None,
                status=UserStatus.PENDING,
                display_name=_normalise_display_name(display_name),
            )
        except IntegrityError as exc:
            # `uq_users_email_active` — the check above is advisory, the
            # constraint is the decision. Closes the TOCTOU window between them.
            raise EmailAlreadyRegistered(email) from exc
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="user.created",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=user.id,
                metadata={"email_digest": recipient_digest(email), "provisioned_by_admin": True},
                request_id=request_id,
            ),
        )
        links = await self._mint_activation_links(
            user,
            admin_user_id=admin_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        return user, links

    async def issue_activation_links(
        self,
        *,
        target_user_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ActivationLinks:
        """Re-mint both activation links for an account that still needs them.

        Exists because the set-password token lives 30 minutes (R6.05) and an
        Admin should not be racing that timer: they click this when they are
        actually with the person, rather than at provisioning time.

        Refused once the account can already authenticate: a verified address
        *and* at least one usable credential. A set-password link for a live
        account is a persistent takeover primitive, and "re-issue activation
        links" is not the place to put one — an Admin who needs to act as a user
        has impersonation, which is bounded and separately audited.

        "Usable credential" has to mean a password **or** a linked identity, not
        just a password. A Google-provisioned account is created with
        ``password_hash=None`` and ``email_verified=True`` (R6.15,
        ``auth_service.login_with_oauth``), and R6.16 neutralises the password of
        an account Google links to — so a password-only test reads every Google
        user as "still needs activation" and hands out a set-password link for a
        fully live account. This is the same notion ``LastCredentialError``
        already encodes for unlinking.
        """
        user = await self._users.get_by_id(target_user_id)
        if user is None or user.deleted_at is not None:
            raise ValueError(f"user {target_user_id} not found")
        identities = await self._identities.list_for_user(target_user_id)
        has_credential = user.password_hash is not None or bool(identities)
        if user.email_verified and has_credential:
            raise AccountAlreadyActivatedError(
                "account is already activated; use password reset or impersonation"
            )
        # SEC: cap the mint per target user so a compromised admin session cannot
        # grind tokens at one account. Reported, not swallowed — see
        # ActivationLinkRateLimited.
        rl = await ratelimit.check_raw(
            key=f"rl:actlink:u:{target_user_id}",
            window_sec=_ACTIVATION_LINK_WINDOW_SEC,
            max_count=_ACTIVATION_LINK_MAX,
        )
        if not rl.allowed:
            raise ActivationLinkRateLimited(rl.retry_after_seconds)
        return await self._mint_activation_links(
            user,
            admin_user_id=admin_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def _mint_activation_links(
        self,
        user: User,
        *,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> ActivationLinks:
        # `issue` burns the target's earlier unused token of the same kind
        # (SEC-L2), so a re-issue leaves exactly one live link per purpose. A
        # token already *consumed* is untouched — this never undoes a completed
        # step.
        reset_token, _ = await self._reset.issue(user.id, _RESET_TTL)
        verify_token, _ = await self._verify.issue(user.id, _VERIFY_TTL)
        issued_at = now()
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.user_activation_links_issued",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=user.id,
                # Digest only. Neither token nor plaintext address may reach the
                # audit log — the tokens are bearer credentials and the address
                # is PII.
                metadata={"recipient_digest": recipient_digest(user.email)},
                request_id=request_id,
            ),
        )
        return ActivationLinks(
            set_password_url=password_reset_url(self._public_origin, reset_token),
            verify_email_url=verify_email_url(self._public_origin, verify_token),
            set_password_expires_at=issued_at + _RESET_TTL,
            verify_email_expires_at=issued_at + _VERIFY_TTL,
        )

    async def search_users(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        cursor: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[User]:
        query = t.users.select().order_by(t.users.c.id.desc()).limit(limit)
        if cursor is not None:
            query = query.where(t.users.c.id < cursor)
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(t.users.c.email.ilike(f"%{escaped}%", escape="\\"))
        if status:
            query = query.where(t.users.c.status == status)
        rows = (await self._db.execute(query)).all()
        return [row_to_user(r) for r in rows]

    async def get_user_detail(self, user_id: uuid.UUID) -> UserDetail | None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            return None
        is_admin = await self._admins.is_admin(user_id)
        org_rows: list[Any] = (  # type: ignore[assignment]
            await self._db.execute(
                sa.select(sa.column("org_id"))
                .select_from(sa.table("org_members"))
                .where(sa.column("user_id") == user_id)
            )
        ).all()
        proj_rows: list[Any] = (  # type: ignore[assignment]
            await self._db.execute(
                sa.select(sa.column("project_id"))
                .select_from(sa.table("project_members"))
                .where(sa.column("user_id") == user_id)
            )
        ).all()
        return UserDetail(
            user=user,
            is_admin=is_admin,
            org_ids=[r[0] for r in org_rows],
            project_ids=[r[0] for r in proj_rows],
        )

    async def _require_user(self, user_id: uuid.UUID) -> None:
        if await self._users.get_by_id(user_id) is None:
            raise ValueError(f"user {user_id} not found")

    async def ban_user(
        self,
        *,
        target_user_id: uuid.UUID,
        reason: str,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        if admin_user_id == target_user_id:
            raise SelfTargetError("Cannot ban yourself")
        await self._require_user(target_user_id)
        await self._users.ban(target_user_id, reason)
        await self._invalidate_user_sessions(target_user_id)
        # Real-time force-logout (R24.19): the frontend's ban-kick guard listens
        # on /ws/user/{id} and redirects to login. Session invalidation alone
        # only takes effect on the victim's next request; this evicts open tabs
        # immediately.
        await Publisher(user_channel(target_user_id)).emit("ban-kick", {"reason": reason})
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.ban_user",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                metadata={"reason": reason},
                request_id=request_id,
            ),
        )
        await NotificationFacade(self._db).send(
            user_id=target_user_id,
            kind=NotificationKind.ADMIN_BAN_REASON,
            title="Your account has been suspended",
            body=reason,
            metadata={"reason": reason},
            dedup_key=f"ban:{target_user_id}:{request_id}" if request_id else None,
        )

    async def unban_user(
        self,
        *,
        target_user_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._require_user(target_user_id)
        await self._users.unban(target_user_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.unban_user",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                request_id=request_id,
            ),
        )

    async def soft_delete_user(
        self,
        *,
        target_user_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        if admin_user_id == target_user_id:
            raise SelfTargetError("Cannot delete yourself")
        await self._require_user(target_user_id)
        from contexts.tenancy.interfaces.facade import TenancyFacade

        tenancy = TenancyFacade(self._db)
        blocked = await tenancy.orgs_blocking_self_delete(target_user_id)
        if blocked:
            raise ValueError(
                f"user is Original Creator of org(s) with active members: "
                f"{', '.join(str(o) for o in blocked)}; transfer OC first"
            )
        cascade_counts = await tenancy.cascade_account_deletion(
            user_id=target_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        await self._users.soft_delete(target_user_id)
        await self._invalidate_user_sessions(target_user_id)
        await Publisher(user_channel(target_user_id)).emit(
            "account-deleted",
            {"by": "admin"},
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.delete_user",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                metadata=cascade_counts,
                request_id=request_id,
            ),
        )

    async def hard_delete_user(
        self,
        *,
        target_user_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        if admin_user_id == target_user_id:
            raise SelfTargetError("Cannot delete yourself")
        from contexts.tenancy.interfaces.facade import TenancyFacade

        user = await self._users.get_by_id(target_user_id)
        if user is None:
            raise ValueError(f"user {target_user_id} not found")
        if user.deleted_at is None:
            raise ValueError("user must be soft-deleted first")
        grace_days = (now() - user.deleted_at).days
        if grace_days < 60:
            raise ValueError(f"60-day grace period not elapsed ({grace_days}d)")
        tenancy = TenancyFacade(self._db)
        blocked = await tenancy.orgs_blocking_self_delete(target_user_id)
        if blocked:
            raise ValueError(
                f"user is Original Creator of org(s) with active members: "
                f"{', '.join(str(o) for o in blocked)}; transfer OC first"
            )
        doomed_projects = await tenancy.prepare_hard_delete(
            user_id=target_user_id,
            reassign_to_user_id=admin_user_id,
        )
        _message_edits = sa.table(
            "message_edits",
            sa.column("edited_by_user_id"),
        )
        await self._db.execute(
            _message_edits.delete().where(_message_edits.c.edited_by_user_id == target_user_id)
        )
        await self._db.execute(t.users.delete().where(t.users.c.id == target_user_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.hard_delete_user",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                request_id=request_id,
            ),
        )
        # F-7: commit the deletion decision before any external erasure. Until
        # this commit the rows can still roll back or be restored, and purging a
        # project's sources for a deletion that never lands destroys data the
        # tenant can never rebuild. `db_session` supports the mid-request commit;
        # its trailing one is then a no-op.
        await self._db.commit()
        await tenancy.purge_hard_deleted_project_sources(doomed_projects)

    async def list_admins(self) -> list[AdminEntry]:
        rows = (
            await self._db.execute(
                t.admins.select()
                .join(t.users, t.admins.c.user_id == t.users.c.id)
                .where(
                    sa.and_(
                        t.admins.c.revoked_at.is_(None),
                        t.users.c.status == "active",
                        t.users.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.admins.c.promoted_at.desc())
            )
        ).all()
        return [
            AdminEntry(
                user_id=r.user_id,
                promoted_by_user_id=r.promoted_by_user_id,
                promoted_at=r.promoted_at,
            )
            for r in rows
        ]

    async def promote_admin(
        self,
        *,
        target_user_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> AdminEntry:
        await self._require_user(target_user_id)
        uid, promoted_by, promoted_at = await self._admins.promote(
            user_id=target_user_id, promoted_by=admin_user_id
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.promote",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                request_id=request_id,
            ),
        )
        return AdminEntry(
            user_id=uid,
            promoted_by_user_id=promoted_by,
            promoted_at=promoted_at,
        )

    async def demote_admin(
        self,
        *,
        target_user_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        admin_ids = await self._admins.list_active_admin_ids(for_update=True)
        if len(admin_ids) <= 1 and target_user_id in admin_ids:
            raise LastAdminError()
        await self._admins.demote(target_user_id)
        # Close active impersonation sessions for the demoted admin and revoke JWTs.
        imp_rows = (
            await self._db.execute(
                t.admin_impersonation_sessions.update()
                .where(
                    sa.and_(
                        t.admin_impersonation_sessions.c.admin_user_id == target_user_id,
                        t.admin_impersonation_sessions.c.ended_at.is_(None),
                    )
                )
                .values(ended_at=now())
                .returning(t.admin_impersonation_sessions.c.access_jti)
            )
        ).all()
        for row in imp_rows:
            if row.access_jti is not None:
                await tokens.deny_access_jti(row.access_jti)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.demote",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                request_id=request_id,
            ),
        )

    async def restore_user(
        self,
        *,
        resource_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted user (R8.13). Clears deleted_at and
        re-activates the account: status back to ACTIVE (or PENDING if the email
        was never verified). A user who was banned *before* being soft-deleted is
        restored back to BANNED with its ban metadata intact — restore reverses a
        deletion, it does not lift a ban (unban is a separate admin action). Emits
        admin.restore_resource. Restoring into an email a live account has since
        taken raises RestoreConflict (route maps to 409)."""
        try:
            result = await self._db.execute(
                t.users.update()
                .where(
                    sa.and_(
                        t.users.c.id == resource_id,
                        t.users.c.deleted_at.isnot(None),
                    )
                )
                .values(deleted_at=None)
            )
        except IntegrityError as exc:
            raise_restore_conflict(exc, unique_constraint="uq_users_email_active", resource_type="user")
        if result.rowcount == 0:
            return False
        await self._db.execute(
            t.users.update()
            .where(t.users.c.id == resource_id)
            .values(
                # A ban survives soft-delete (soft_delete preserves banned_reason/
                # banned_at), so a non-null banned_reason means this account was
                # banned before deletion — restore it straight back to BANNED rather
                # than silently reactivating it. Otherwise ACTIVE/PENDING by email
                # verification. Ban metadata is left untouched (never nulled here).
                status=sa.case(
                    (t.users.c.banned_reason.isnot(None), UserStatus.BANNED.value),
                    (t.users.c.email_verified == True, UserStatus.ACTIVE.value),  # noqa: E712
                    else_=UserStatus.PENDING.value,
                ),
            )
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.restore_resource",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=resource_id,
                request_id=request_id,
            ),
        )
        return True

    async def _invalidate_user_sessions(self, user_id: uuid.UUID) -> None:
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


__all__ = [
    "AccountAlreadyActivatedError",
    "ActivationLinks",
    "AdminEntry",
    "AdminService",
    "LastAdminError",
    "SelfTargetError",
    "UserDetail",
]
