"""Admin impersonation (view-as) service (I.5).

Creates a scoped read-only JWT with `impersonated_by` claim, tracks the
session in `admin_impersonation_sessions`, and auto-expires after 30 min.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.infrastructure import tables as t
from shared_kernel import audit
from shared_kernel.auth import jwt, tokens
from shared_kernel.auth.clients import now


@dataclass(frozen=True, slots=True)
class ImpersonationSession:
    id: uuid.UUID
    admin_user_id: uuid.UUID
    target_user_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None


class ImpersonationService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def start(
        self,
        *,
        admin_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> tuple[ImpersonationSession, str]:
        from contexts.identity.application.admin_service import SelfTargetError

        if admin_user_id == target_user_id:
            raise SelfTargetError("Cannot impersonate yourself")
        target_exists = (
            await self._db.execute(t.users.select().where(t.users.c.id == target_user_id))
        ).first()
        if target_exists is None:
            raise ValueError(f"user {target_user_id} not found")

        active = (
            await self._db.execute(
                t.admin_impersonation_sessions.select().where(
                    sa.and_(
                        t.admin_impersonation_sessions.c.admin_user_id == admin_user_id,
                        t.admin_impersonation_sessions.c.ended_at.is_(None),
                    )
                )
            )
        ).first()
        if active is not None:
            old = (
                await self._db.execute(
                    t.admin_impersonation_sessions.update()
                    .where(t.admin_impersonation_sessions.c.id == active.id)
                    .values(ended_at=now())
                    .returning(t.admin_impersonation_sessions.c.access_jti)
                )
            ).one_or_none()
            if old is not None and old.access_jti is not None:
                await tokens.deny_access_jti(old.access_jti)

        session_id = uuid.uuid4()
        access_token, claims = jwt.sign_access_token(
            user_id=target_user_id,
            session_id=session_id,
            is_admin=False,
            role="impersonation",
            extra={"impersonated_by": str(admin_user_id)},
        )

        row = (
            await self._db.execute(
                t.admin_impersonation_sessions.insert()
                .values(
                    admin_user_id=admin_user_id,
                    target_user_id=target_user_id,
                    started_request_id=request_id,
                    access_jti=claims.jti,
                )
                .returning(t.admin_impersonation_sessions)
            )
        ).one()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.view_as_started",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                metadata={"impersonation_session_id": str(row.id)},
                request_id=request_id,
            ),
        )

        session = ImpersonationSession(
            id=row.id,
            admin_user_id=row.admin_user_id,
            target_user_id=row.target_user_id,
            started_at=row.started_at,
            ended_at=row.ended_at,
        )
        return session, access_token

    async def end(
        self,
        *,
        admin_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        row = (
            await self._db.execute(
                t.admin_impersonation_sessions.update()
                .where(
                    sa.and_(
                        t.admin_impersonation_sessions.c.admin_user_id == admin_user_id,
                        t.admin_impersonation_sessions.c.target_user_id == target_user_id,
                        t.admin_impersonation_sessions.c.ended_at.is_(None),
                    )
                )
                .values(ended_at=now())
                .returning(t.admin_impersonation_sessions.c.access_jti)
            )
        ).one_or_none()
        if row is None:
            return False
        if row.access_jti is not None:
            await tokens.deny_access_jti(row.access_jti)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.view_as_ended",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="user",
                resource_id=target_user_id,
                request_id=request_id,
            ),
        )
        return True

    async def close_idle_sessions(self, idle_minutes: int = 30) -> int:
        cutoff = now() - timedelta(minutes=idle_minutes)
        rows = (
            await self._db.execute(
                t.admin_impersonation_sessions.update()
                .where(
                    sa.and_(
                        t.admin_impersonation_sessions.c.ended_at.is_(None),
                        t.admin_impersonation_sessions.c.started_at < cutoff,
                    )
                )
                .values(ended_at=now())
                .returning(t.admin_impersonation_sessions.c.access_jti)
            )
        ).all()
        # S7: revoke the access token for each closed session, matching end().
        for row in rows:
            if row.access_jti is not None:
                await tokens.deny_access_jti(row.access_jti)
        return len(rows)

    async def list_active(self) -> list[ImpersonationSession]:
        rows = (
            await self._db.execute(
                t.admin_impersonation_sessions.select()
                .where(t.admin_impersonation_sessions.c.ended_at.is_(None))
                .order_by(t.admin_impersonation_sessions.c.started_at.desc())
            )
        ).all()
        return [
            ImpersonationSession(
                id=r.id,
                admin_user_id=r.admin_user_id,
                target_user_id=r.target_user_id,
                started_at=r.started_at,
                ended_at=r.ended_at,
            )
            for r in rows
        ]


__all__ = ["ImpersonationService", "ImpersonationSession"]
