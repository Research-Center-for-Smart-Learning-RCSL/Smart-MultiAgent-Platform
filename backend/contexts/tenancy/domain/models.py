"""Tenancy domain dataclasses — framework-free."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime


class OrgMemberRole(str, enum.Enum):
    OWNER = "owner"
    MEMBER = "member"


class ProjectMemberRole(str, enum.Enum):
    OWNER = "owner"
    MEMBER = "member"


class InviteScope(str, enum.Enum):
    ORG = "org"
    PROJECT = "project"


class InviteState(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ProjectOwnerType(str, enum.Enum):
    USER = "user"
    ORG = "org"


class OCTransferState(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    ADMIN_FORCED = "admin_forced"


@dataclass(frozen=True, slots=True)
class Org:
    id: uuid.UUID
    name: str
    creator_user_id: uuid.UUID
    version: int
    deleted_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrgMember:
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: OrgMemberRole
    is_original_creator: bool
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class Project:
    id: uuid.UUID
    owner_user_id: uuid.UUID | None
    owner_org_id: uuid.UUID | None
    name: str
    created_by_user_id: uuid.UUID
    version: int
    deleted_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectMember:
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: ProjectMemberRole
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class Invite:
    id: uuid.UUID
    scope_type: InviteScope
    scope_id: uuid.UUID
    role: str
    inviter_user_id: uuid.UUID | None
    invitee_email: str
    invitee_user_id: uuid.UUID | None
    state: InviteState
    token_hash: str
    expires_at: datetime
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class OCTransfer:
    id: uuid.UUID
    org_id: uuid.UUID
    initiator_user_id: uuid.UUID
    target_user_id: uuid.UUID
    state: OCTransferState
    created_at: datetime
    expires_at: datetime
    resolved_at: datetime | None


__all__ = [
    "Invite",
    "InviteScope",
    "InviteState",
    "OCTransfer",
    "OCTransferState",
    "Org",
    "OrgMember",
    "OrgMemberRole",
    "Project",
    "ProjectMember",
    "ProjectMemberRole",
    "ProjectOwnerType",
]
