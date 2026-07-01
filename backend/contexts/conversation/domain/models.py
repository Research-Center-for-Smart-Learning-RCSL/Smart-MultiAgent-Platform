"""Conversation domain dataclasses — framework-free."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class SenderType(str, enum.Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AttachmentStatus(str, enum.Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"


class ScanStatus(str, enum.Enum):
    PENDING = "pending"
    CLEAN = "clean"
    QUARANTINED = "quarantined"
    SKIPPED = "skipped"


class AttachmentExtractionStatus(str, enum.Enum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Workspace:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    created_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class Chatroom:
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    allow_org_members: bool
    allow_project_members: bool
    allow_project_owners_only: bool
    allow_guest_links: bool
    guest_token: str
    version: int
    created_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class ChatroomAgent:
    chatroom_id: uuid.UUID
    agent_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ChatroomGuest:
    chatroom_id: uuid.UUID
    user_id: uuid.UUID
    joined_via_token: str
    display_name: str | None
    joined_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    id: uuid.UUID
    chatroom_id: uuid.UUID
    sender_type: SenderType
    sender_id: uuid.UUID | None
    content_md: str
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: datetime | None = None
    edited_at: datetime | None = None
    deleted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MessageEdit:
    id: uuid.UUID
    message_id: uuid.UUID
    old_content_md: str
    edited_by_user_id: uuid.UUID
    edited_at: datetime


@dataclass(frozen=True, slots=True)
class MessageAttachment:
    id: uuid.UUID
    message_id: uuid.UUID | None
    filename: str
    mime: str
    size_bytes: int
    minio_path: str
    status: AttachmentStatus
    scan_status: ScanStatus
    scan_at: datetime | None
    expires_at: datetime | None
    chatroom_id: uuid.UUID | None = None
    uploaded_by_user_id: uuid.UUID | None = None
    extracted_text: str | None = None
    extraction_status: AttachmentExtractionStatus = AttachmentExtractionStatus.PENDING
    extracted_at: datetime | None = None


__all__ = [
    "AttachmentExtractionStatus",
    "AttachmentStatus",
    "Chatroom",
    "ChatroomAgent",
    "ChatroomGuest",
    "Message",
    "MessageAttachment",
    "MessageEdit",
    "ScanStatus",
    "SenderType",
    "Workspace",
]
