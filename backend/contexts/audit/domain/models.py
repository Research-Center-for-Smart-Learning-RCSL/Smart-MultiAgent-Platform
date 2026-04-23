"""Audit domain dataclasses — query-side only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    actor_user_id: uuid.UUID | None
    actor_ip: str | None
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    metadata: dict[str, Any]
    session_id: uuid.UUID | None
    request_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditFilter:
    actor_user_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    action: str | None = None
    from_ts: datetime | None = None
    to_ts: datetime | None = None
    ip_prefix: str | None = None
    session_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class AuditPage:
    items: list[AuditEntry]
    next_cursor: int | None


__all__ = ["AuditEntry", "AuditFilter", "AuditPage"]
