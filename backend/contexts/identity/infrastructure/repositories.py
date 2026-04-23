"""Repositories for the identity context.

Each repository wraps one Table and returns domain dataclasses. Queries are
kept narrow — no `select(*)` against the world. Cross-context joins are
forbidden (R23.01); anything that needs tenancy data goes through the
tenancy facade.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.domain.models import (
    EmailVerifyToken,
    IpBan,
    PasswordResetToken,
    Session,
    User,
    UserStatus,
)
from contexts.identity.infrastructure import tables as t
from shared_kernel.auth.clients import now


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


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self, *, email: str, password_hash: str, status: UserStatus = UserStatus.PENDING
    ) -> User:
        stmt = (
            t.users.insert()
            .values(email=email, password_hash=password_hash, status=status.value)
            .returning(t.users)
        )
        row = (await self._db.execute(stmt)).one()
        return _row_to_user(row)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        row = (
            await self._db.execute(t.users.select().where(t.users.c.id == user_id))
        ).first()
        return _row_to_user(row) if row else None

    async def get_active_by_email(self, email: str) -> User | None:
        row = (
            await self._db.execute(
                t.users.select().where(
                    sa.and_(
                        t.users.c.email == email,
                        t.users.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        return _row_to_user(row) if row else None

    async def set_password(self, user_id: uuid.UUID, password_hash: str) -> None:
        await self._db.execute(
            t.users.update()
            .where(t.users.c.id == user_id)
            .values(password_hash=password_hash)
        )

    async def set_email(self, user_id: uuid.UUID, new_email: str) -> None:
        # Only demote active/pending users to pending on email change. If the
        # account is banned or deleted, preserve that status — we must never
        # let an email-change flow resurrect a banned user.
        await self._db.execute(
            t.users.update()
            .where(
                sa.and_(
                    t.users.c.id == user_id,
                    t.users.c.status.in_(
                        [UserStatus.ACTIVE.value, UserStatus.PENDING.value]
                    ),
                )
            )
            .values(
                email=new_email,
                email_verified=False,
                status=UserStatus.PENDING.value,
            )
        )

    async def mark_verified(self, user_id: uuid.UUID) -> None:
        # Only promote pending → active. A banned / deleted account must NOT
        # be reactivated by a late-arriving verification token.
        await self._db.execute(
            t.users.update()
            .where(
                sa.and_(
                    t.users.c.id == user_id,
                    t.users.c.status == UserStatus.PENDING.value,
                )
            )
            .values(email_verified=True, status=UserStatus.ACTIVE.value)
        )

    async def mark_logged_in(self, user_id: uuid.UUID) -> None:
        await self._db.execute(
            t.users.update()
            .where(t.users.c.id == user_id)
            .values(last_login_at=now())
        )

    async def soft_delete(self, user_id: uuid.UUID) -> None:
        await self._db.execute(
            t.users.update()
            .where(t.users.c.id == user_id)
            .values(deleted_at=now(), status=UserStatus.DELETED.value)
        )

    async def ban(self, user_id: uuid.UUID, reason: str) -> None:
        await self._db.execute(
            t.users.update()
            .where(t.users.c.id == user_id)
            .values(status=UserStatus.BANNED.value, banned_reason=reason, banned_at=now())
        )

    async def unban(self, user_id: uuid.UUID) -> None:
        await self._db.execute(
            t.users.update()
            .where(t.users.c.id == user_id)
            .values(status=UserStatus.ACTIVE.value, banned_reason=None, banned_at=None)
        )


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        family_id: uuid.UUID,
        refresh_token_hash: str,
        user_agent: str | None,
        ip_inet: str | None,
        last_jti: uuid.UUID | None,
        expires_at: datetime,
    ) -> Session:
        stmt = (
            t.sessions.insert()
            .values(
                id=session_id,
                user_id=user_id,
                family_id=family_id,
                refresh_token_hash=refresh_token_hash,
                user_agent=user_agent,
                ip_inet=ip_inet,
                last_jti=last_jti,
                expires_at=expires_at,
            )
            .returning(t.sessions)
        )
        row = (await self._db.execute(stmt)).one()
        return _row_to_session(row)

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Session]:
        rows = (
            await self._db.execute(
                t.sessions.select()
                .where(
                    sa.and_(
                        t.sessions.c.user_id == user_id,
                        t.sessions.c.revoked_at.is_(None),
                        t.sessions.c.expires_at > now(),
                    )
                )
                .order_by(t.sessions.c.last_used_at.desc())
            )
        ).all()
        return [_row_to_session(r) for r in rows]

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None:
        row = (
            await self._db.execute(
                t.sessions.select().where(t.sessions.c.id == session_id)
            )
        ).first()
        return _row_to_session(row) if row else None

    async def update_on_rotation(
        self,
        *,
        old_hash: str,
        new_hash: str,
        new_jti: uuid.UUID,
    ) -> None:
        await self._db.execute(
            t.sessions.update()
            .where(t.sessions.c.refresh_token_hash == old_hash)
            .values(
                refresh_token_hash=new_hash,
                last_jti=new_jti,
                last_used_at=now(),
            )
        )

    async def revoke(self, *, session_id: uuid.UUID) -> None:
        await self._db.execute(
            t.sessions.update()
            .where(t.sessions.c.id == session_id)
            .values(revoked_at=now())
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        await self._db.execute(
            t.sessions.update()
            .where(
                sa.and_(
                    t.sessions.c.user_id == user_id,
                    t.sessions.c.revoked_at.is_(None),
                )
            )
            .values(revoked_at=now())
        )


def _row_to_session(row: Any) -> Session:
    return Session(
        id=row.id,
        user_id=row.user_id,
        family_id=row.family_id,
        refresh_token_hash=row.refresh_token_hash,
        user_agent=row.user_agent,
        ip_inet=str(row.ip_inet) if row.ip_inet is not None else None,
        last_jti=row.last_jti,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


# ---------- Token repos (shared pattern for email-verify + password-reset) ----


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


class _TokenRepo:
    """Base for the two single-use token tables."""

    _table: sa.Table  # set by subclass

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def issue(self, user_id: uuid.UUID, ttl: timedelta) -> tuple[str, str]:
        """Return (plaintext_token, hash) — plaintext goes in the email body."""
        token = _new_token()
        token_hash = _hash_token(token)
        await self._db.execute(
            self._table.insert().values(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=now() + ttl,
            )
        )
        return token, token_hash

    async def consume(self, token: str) -> tuple[uuid.UUID, uuid.UUID] | None:
        """Atomically mark-used and return (token_id, user_id) on success."""
        token_hash = _hash_token(token)
        stmt = (
            self._table.update()
            .where(
                sa.and_(
                    self._table.c.token_hash == token_hash,
                    self._table.c.used_at.is_(None),
                    self._table.c.expires_at > now(),
                )
            )
            .values(used_at=now())
            .returning(self._table.c.id, self._table.c.user_id)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            return None
        return row.id, row.user_id


class EmailVerifyTokenRepository(_TokenRepo):
    _table = t.email_verify_tokens


class PasswordResetTokenRepository(_TokenRepo):
    _table = t.password_reset_tokens


# ---------- Admin + IP ban repos ---------------------------------------------


class AdminRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def is_admin(self, user_id: uuid.UUID) -> bool:
        row = (
            await self._db.execute(
                t.admins.select().where(
                    sa.and_(
                        t.admins.c.user_id == user_id,
                        t.admins.c.revoked_at.is_(None),
                    )
                )
            )
        ).first()
        return row is not None

    async def list_admin_ids(self) -> set[uuid.UUID]:
        rows = (
            await self._db.execute(
                sa.select(t.admins.c.user_id).where(t.admins.c.revoked_at.is_(None))
            )
        ).all()
        return {r.user_id for r in rows}

    async def promote(
        self, *, user_id: uuid.UUID, promoted_by: uuid.UUID | None
    ) -> tuple[uuid.UUID, uuid.UUID | None, datetime]:
        stmt = (
            pg_insert(t.admins)
            .values(user_id=user_id, promoted_by_user_id=promoted_by)
            .on_conflict_do_update(
                index_elements=[t.admins.c.user_id],
                set_={"revoked_at": None, "promoted_by_user_id": promoted_by,
                      "promoted_at": sa.func.now()},
            )
            .returning(
                t.admins.c.user_id,
                t.admins.c.promoted_by_user_id,
                t.admins.c.promoted_at,
            )
        )
        row = (await self._db.execute(stmt)).one()
        return row.user_id, row.promoted_by_user_id, row.promoted_at

    async def demote(self, user_id: uuid.UUID) -> None:
        await self._db.execute(
            t.admins.update()
            .where(t.admins.c.user_id == user_id)
            .values(revoked_at=now())
        )


class IpBanRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_all(self) -> Sequence[IpBan]:
        rows = (await self._db.execute(t.ip_bans.select())).all()
        return [
            IpBan(
                id=r.id,
                cidr=str(r.cidr),
                reason=r.reason,
                banned_at=r.banned_at,
                created_by_user_id=r.created_by_user_id,
            )
            for r in rows
        ]

    async def insert(
        self, *, cidr: str, reason: str, created_by: uuid.UUID | None
    ) -> IpBan:
        # Validate before we round-trip; Postgres cidr type would reject but we
        # want a stable Python-level error path.
        ipaddress.ip_network(cidr, strict=False)
        row = (
            await self._db.execute(
                t.ip_bans.insert()
                .values(cidr=cidr, reason=reason, created_by_user_id=created_by)
                .returning(t.ip_bans)
            )
        ).one()
        return IpBan(
            id=row.id,
            cidr=str(row.cidr),
            reason=row.reason,
            banned_at=row.banned_at,
            created_by_user_id=row.created_by_user_id,
        )

    async def delete(self, ban_id: uuid.UUID) -> None:
        await self._db.execute(t.ip_bans.delete().where(t.ip_bans.c.id == ban_id))


__all__ = [
    "AdminRepository",
    "EmailVerifyTokenRepository",
    "IpBanRepository",
    "PasswordResetTokenRepository",
    "SessionRepository",
    "UserRepository",
]
