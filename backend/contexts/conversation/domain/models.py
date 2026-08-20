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


class ExportSenderScope(str, enum.Enum):
    """How much of a readable room a chat export may carry (matrix row 19).

    ALL                — every sender, for owners and admins (the `✓` cells).
    OWN_PLUS_NON_USER  — the caller's own messages plus all agent and system
                         messages, for members (the `∘` cells). Other users'
                         messages, edit histories and attachments are excluded.

    Ordering matters: ALL is strictly wider than OWN_PLUS_NON_USER, and the
    export worker refuses to widen past the scope recorded at request time.
    """

    ALL = "all"
    OWN_PLUS_NON_USER = "own_plus_non_user"


class ChatroomAgentRole(str, enum.Enum):
    NORMAL = "normal"
    OBSERVER = "observer"


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
    # Wide-layer Concept Map privacy opt-in (R11.10). Off by default.
    concept_map_enabled: bool = False


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
    created_by_user_id: uuid.UUID | None = None
    disclose_observers: bool = True
    # §13.2a. Defaulted so every existing construction site — tests included —
    # keeps compiling and keeps meaning "this room has no group tier".
    allow_member_groups: bool = False


@dataclass(frozen=True, slots=True)
class ChatroomAgent:
    chatroom_id: uuid.UUID
    agent_id: uuid.UUID
    role: ChatroomAgentRole = ChatroomAgentRole.NORMAL
    # Delegated activity control ([R30.37]). Defaulted so the construction sites
    # that predate the grant keep describing the case they describe: an ordinary
    # binding holds no authority, which is exactly what these defaults say.
    #
    # A non-empty allowlist with `may_control_activities` false is a legal and
    # deliberate state — the teacher's selection, remembered across a revoke. Read
    # `may_control_activities` first; never infer the grant from the allowlist.
    may_control_activities: bool = False
    activity_type_allowlist: tuple[uuid.UUID, ...] = ()
    granted_by_user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ActivityControlGrant:
    """A live delegation of activity start/end authority in one room ([R30.37]).

    Only ever constructed for a binding whose ``may_control_activities`` is true, so
    holding one *is* the authorization — there is no "granted: false" variant to
    check. ``granted_by_user_id`` is the user on whose authority the agent acts and
    is what an activation started under this grant records as its starting user.

    ``activity_type_ids`` is the stored allowlist, unresolved: an id here may point
    at a type that was since deleted or became unreachable from the room's project.
    Every consumer resolves each id through the activities facade before offering or
    acting on it.
    """

    agent_id: uuid.UUID
    granted_by_user_id: uuid.UUID
    activity_type_ids: tuple[uuid.UUID, ...]


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
class AgentObservation:
    id: uuid.UUID
    chatroom_id: uuid.UUID
    agent_id: uuid.UUID
    content_md: str
    trigger: str
    metadata: dict[str, Any] = field(default_factory=dict)
    trigger_message_id: uuid.UUID | None = None
    released_at: datetime | None = None
    release_target: dict[str, Any] | None = None
    released_by_user_id: uuid.UUID | None = None
    created_at: datetime | None = None
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
    "ActivityControlGrant",
    "AgentObservation",
    "AttachmentExtractionStatus",
    "AttachmentStatus",
    "Chatroom",
    "ChatroomAgent",
    "ChatroomAgentRole",
    "ChatroomGuest",
    "Message",
    "MessageAttachment",
    "MessageEdit",
    "ScanStatus",
    "SenderType",
    "Workspace",
]
