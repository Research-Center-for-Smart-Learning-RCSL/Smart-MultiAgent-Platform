"""IP-ban CRUD + audit emission (R6.13, R19.05).

Router calls this; never reaches into `IpBanRepository` directly. Every write
emits an `admin.ban_ip` / `admin.unban_ip` audit event.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.domain.models import IpBan
from contexts.identity.infrastructure.repositories import IpBanRepository
from shared_kernel import audit


class IpBanService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = IpBanRepository(db)

    async def add(
        self,
        *,
        cidr: str,
        reason: str,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> IpBan:
        ban = await self._repo.insert(
            cidr=cidr,
            reason=reason,
            created_by=admin_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.ban_ip",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="ip_ban",
                resource_id=ban.id,
                metadata={"cidr": cidr, "reason": reason},
                request_id=request_id,
            ),
        )
        return ban

    async def remove(
        self,
        *,
        ban_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._repo.delete(ban_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.unban_ip",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="ip_ban",
                resource_id=ban_id,
                request_id=request_id,
            ),
        )


__all__ = ["IpBanService"]
