"""Search-key domain types (D.11 / §12.4)."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from contexts.keys.domain.probe_status import ProbeStatus


class SearchProvider(str, enum.Enum):
    BRAVE = "brave"
    SERPER = "serper"
    TAVILY = "tavily"
    GOOGLE_CSE = "google_cse"


@dataclass(frozen=True, slots=True)
class SearchKey:
    id: uuid.UUID
    project_id: uuid.UUID
    provider: SearchProvider
    masked_preview: str
    test_status: ProbeStatus
    test_error: str | None
    last_test_at: datetime | None
    is_active: bool
    config: dict[str, Any]
    transit_key_version: int
    hmac_key_version: int
    created_at: datetime
    deleted_at: datetime | None


__all__ = ["SearchKey", "SearchProvider"]
