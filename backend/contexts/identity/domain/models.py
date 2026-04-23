"""Identity domain dataclasses.

These are *plain* records: no SQLAlchemy, no Pydantic, no framework types.
Application services pass these across the domain→infrastructure boundary;
routers translate to/from pydantic schemas at the edge.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    PENDING = "pending"
    BANNED = "banned"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class User:
    id: uuid.UUID
    email: str
    password_hash: str
    email_verified: bool
    status: UserStatus
    banned_reason: str | None
    banned_at: datetime | None
    deleted_at: datetime | None
    last_login_at: datetime | None
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Session:
    id: uuid.UUID
    user_id: uuid.UUID
    family_id: uuid.UUID
    refresh_token_hash: str
    user_agent: str | None
    ip_inet: str | None
    last_jti: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class EmailVerifyToken:
    id: uuid.UUID
    user_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PasswordResetToken:
    id: uuid.UUID
    user_id: uuid.UUID
    token_hash: str
    expires_at: datetime
    used_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IpBan:
    id: uuid.UUID
    cidr: str
    reason: str
    banned_at: datetime
    created_by_user_id: uuid.UUID | None


__all__ = [
    "EmailVerifyToken",
    "IpBan",
    "PasswordResetToken",
    "Session",
    "User",
    "UserStatus",
]
