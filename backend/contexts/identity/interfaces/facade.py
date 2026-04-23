"""Identity facade — the *only* surface other contexts (and the web layer)
may use to ask identity questions without reaching into the DB.

Consumers live in `app.api.*` or `shared_kernel.auth.dependencies`. Other
contexts must NOT import this file (import-linter enforces cross-context
independence — cross-context reads go through the `shared_kernel` bus).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.domain.models import User, UserStatus
from contexts.identity.infrastructure.repositories import (
    AdminRepository,
    IpBanRepository,
    UserRepository,
)


@dataclass(frozen=True, slots=True)
class UserProfile:
    id: uuid.UUID
    email: str
    status: UserStatus
    email_verified: bool
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None


class IdentityFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._users = UserRepository(db)
        self._admins = AdminRepository(db)
        self._ip_bans = IpBanRepository(db)

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        return await self._users.get_by_id(user_id)

    async def get_profile(self, user_id: uuid.UUID) -> UserProfile | None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            return None
        return UserProfile(
            id=user.id,
            email=user.email,
            status=user.status,
            email_verified=user.email_verified,
            is_admin=await self._admins.is_admin(user.id),
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    async def is_admin(self, user_id: uuid.UUID) -> bool:
        return await self._admins.is_admin(user_id)

    async def admin_ids(self) -> set[uuid.UUID]:
        return await self._admins.list_admin_ids()

    async def list_ip_bans(self):
        return await self._ip_bans.list_all()


__all__ = ["IdentityFacade", "UserProfile"]
