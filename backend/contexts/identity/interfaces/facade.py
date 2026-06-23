"""Identity facade — the *only* surface other contexts (and the web layer)
may use to ask identity questions without reaching into the DB.

Consumers live in `app.api.*` or `shared_kernel.auth.dependencies`. Other
contexts must NOT import this file (import-linter enforces cross-context
independence — cross-context reads go through the `shared_kernel` bus).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.domain.models import IpBan, User, UserStatus
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
        return await self._admins.list_active_admin_ids()

    async def list_ip_bans(self) -> Sequence[IpBan]:
        return await self._ip_bans.list_all()

    async def send_invite_email(
        self,
        *,
        to_email: str,
        scope_label: str,
        scope_name: str,
        invite_token: str,
        base_url: str,
    ) -> None:
        """Render the invite template and send via the configured transport.

        Encapsulates the identity email infrastructure so callers in other
        contexts (tenancy invite service) do not need to import email
        templates, ``EmailMessage``, or the sender factory directly.
        """
        from contexts.identity.application.factory import (
            LazyEmailSender,
            email_sender_factory,
        )
        from contexts.identity.infrastructure import email_templates
        from contexts.identity.infrastructure.email import EmailMessage

        accept_link = f"{base_url.rstrip('/')}/invites/accept#token={invite_token}"
        rendered = email_templates.invite(
            scope_label=scope_label,
            scope_name=scope_name,
            accept_link=accept_link,
        )
        sender = LazyEmailSender(email_sender_factory)
        await sender.send(
            EmailMessage(
                to=to_email,
                subject=rendered.subject,
                text_body=rendered.text_body,
                html_body=rendered.html_body,
                template="invite",
            )
        )

    @staticmethod
    def recipient_digest(addr: str) -> str:
        """SHA-256 digest of a normalised recipient (re-exported for audit)."""
        from contexts.identity.infrastructure.email import (
            recipient_digest as _digest,
        )

        return _digest(addr)


__all__ = ["IdentityFacade", "UserProfile"]
