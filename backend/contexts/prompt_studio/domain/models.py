"""Domain models for the prompt_studio context (§29).

Pure Python — frozen, slotted dataclasses and str-enums; no framework imports.
Validation bounds are enforced at the API boundary (Pydantic); the constants
here are the single source of truth those request models and the session
builder reference.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

# --- Bounds (§29 / R29.03, R29.06, R29.10) ---------------------------------
SYSTEM_PROMPT_MAX = 20_000
TEMPLATE_NAME_MAX = 100
TEMPLATE_DESC_MAX = 300
TEMPLATE_BODY_MAX = 100_000
TEMPLATES_PER_SCOPE_MAX = 100
DEFAULT_DAILY_REQUEST_LIMIT = 50

FILE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per reference file
EXTRACTED_TEXT_BUDGET = 200 * 1024  # 200 KB total extracted text per config
ALLOWED_FILE_EXTENSIONS: frozenset[str] = frozenset({"pdf", "docx", "md", "txt"})

# --- Session (§29 / R29.07, R29.09) ----------------------------------------
SESSION_TTL_SECONDS = 2 * 60 * 60  # ephemeral, 2 h
SESSION_MAX_MESSAGES = 40  # user + assistant turns combined
MAX_USER_MESSAGE_CHARS = 32_000
ASSISTANT_MAX_TOKENS = 2_048  # per-reply output bound (platform-key abuse guard)


class PromptScope(str, enum.Enum):
    PLATFORM = "platform"
    ORG = "org"
    USER = "user"


class ScanStatus(str, enum.Enum):
    PENDING = "pending"
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AssistantConfig:
    id: uuid.UUID
    scope: PromptScope
    org_id: uuid.UUID | None
    user_id: uuid.UUID | None
    system_prompt: str
    key_id: uuid.UUID | None
    model_id: str | None
    daily_request_limit_per_user: int
    enabled: bool
    hide_platform_templates: bool
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssistantFile:
    id: uuid.UUID
    config_id: uuid.UUID
    filename: str
    size_bytes: int
    sha256: str
    mime: str
    minio_key: str
    scan_status: ScanStatus
    extracted_chars: int
    extracted_text: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    id: uuid.UUID
    scope: PromptScope
    org_id: uuid.UUID | None
    user_id: uuid.UUID | None
    name: str
    description: str
    body: str
    position: int
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SessionMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class AssistantSession:
    """Ephemeral assistant conversation (Redis-backed, TTL-bounded)."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    messages: tuple[SessionMessage, ...]


@dataclass(frozen=True, slots=True)
class TemplateDraft:
    """Partial-update payload for a template PATCH — None means 'unchanged'."""

    name: str | None = None
    description: str | None = None
    body: str | None = None
    position: int | None = None


__all__ = [
    "ALLOWED_FILE_EXTENSIONS",
    "DEFAULT_DAILY_REQUEST_LIMIT",
    "EXTRACTED_TEXT_BUDGET",
    "FILE_MAX_BYTES",
    "SYSTEM_PROMPT_MAX",
    "TEMPLATES_PER_SCOPE_MAX",
    "TEMPLATE_BODY_MAX",
    "TEMPLATE_DESC_MAX",
    "TEMPLATE_NAME_MAX",
    "ASSISTANT_MAX_TOKENS",
    "MAX_USER_MESSAGE_CHARS",
    "SESSION_MAX_MESSAGES",
    "SESSION_TTL_SECONDS",
    "AssistantConfig",
    "AssistantFile",
    "AssistantSession",
    "PromptScope",
    "PromptTemplate",
    "ScanStatus",
    "SessionMessage",
    "TemplateDraft",
]
