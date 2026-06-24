"""Admin user-management service (I.1).

Orchestrates user ban/unban/delete/restore, admin promote/demote with
last-admin guard, and user search. Each write emits an audit event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.identity.domain.models import User, UserStatus
from contexts.identity.infrastructure import tables as t
from contexts.identity.infrastructure.channels import user_channel
from contexts.identity.infrastructure.repositories import (
    AdminRepository,
    SessionRepository,
    UserRepository,
)
from contexts.notification.interfaces.facade import NotificationFacade, NotificationKind
from shared_kernel import audit
from shared_kernel.auth import tokens
from shared_kernel.auth.clients import now
from shared_kernel.realtime.pubsub import Publisher


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


class LastAdminError(Exception):
    pass


class SelfTargetError(ValueError):
    pass


class AdminService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._users = UserRepository(db)
        self._admins = AdminRepository(db)
        self._sessions = SessionRepository(db)

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
        return [_row_to_user(r) for r in rows]

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

        cascade_counts = await TenancyFacade(self._db).cascade_account_deletion(
            user_id=target_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        await self._users.soft_delete(target_user_id)
        await self._invalidate_user_sessions(target_user_id)
        await Publisher(user_channel(target_user_id)).emit(
            "account-deleted", {"by": "admin"},
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
        blocked = await TenancyFacade(self._db).orgs_blocking_self_delete(target_user_id)
        if blocked:
            raise ValueError(
                f"user is Original Creator of org(s) with active members: "
                f"{', '.join(str(o) for o in blocked)}; transfer OC first"
            )
        tenancy = TenancyFacade(self._db)
        await tenancy.prepare_hard_delete(
            user_id=target_user_id,
            reassign_to_user_id=admin_user_id,
        )
        await self._db.execute(
            sa.text("DELETE FROM message_edits WHERE edited_by_user_id = :uid"),
            {"uid": target_user_id},
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

    async def restore_resource(
        self,
        *,
        resource_type: str,
        resource_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        table_map: dict[str, Any] = {
            "user": t.users,
            "org": sa.table("orgs", sa.column("id"), sa.column("deleted_at")),
            "project": sa.table("projects", sa.column("id"), sa.column("deleted_at")),
        }
        tbl = table_map.get(resource_type)
        if tbl is None:
            return False
        result = await self._db.execute(
            tbl.update()
            .where(
                sa.and_(
                    tbl.c.id == resource_id,
                    tbl.c.deleted_at.isnot(None),
                )
            )
            .values(deleted_at=None)
        )
        if result.rowcount == 0:
            return False
        if resource_type == "user":
            await self._db.execute(
                t.users.update()
                .where(t.users.c.id == resource_id)
                .values(
                    status=sa.case(
                        (t.users.c.email_verified == True, UserStatus.ACTIVE.value),  # noqa: E712
                        else_=UserStatus.PENDING.value,
                    ),
                    banned_reason=None,
                    banned_at=None,
                )
            )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.restore_resource",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type=resource_type,
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


def _row_to_user(row: Any) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        email_verified=row.email_verified,
        status=UserStatus(row.status),
        banned_reason=row.banned_reason,
        banned_at=row.banned_at,
        deleted_at=row.deleted_at,
        last_login_at=row.last_login_at,
        version=row.version,
        created_at=row.created_at,
    )


__all__ = ["AdminEntry", "AdminService", "LastAdminError", "SelfTargetError", "UserDetail"]
