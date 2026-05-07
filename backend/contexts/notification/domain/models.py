"""Notification domain dataclasses (R18.01–R18.03)."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class NotificationKind(str, enum.Enum):
    KEY_USAGE_THRESHOLD = "key.usage_threshold"
    KEY_TEST_FAILED = "key.test_failed"
    INVITE_RECEIVED = "invite.received"
    APPROVAL_HUMAN_REQUESTED = "approval.human_requested"
    ADMIN_BAN_REASON = "admin.ban_reason"


@dataclass(frozen=True, slots=True)
class Notification:
    id: uuid.UUID
    user_id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str | None
    metadata: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


__all__ = ["Notification", "NotificationKind"]
