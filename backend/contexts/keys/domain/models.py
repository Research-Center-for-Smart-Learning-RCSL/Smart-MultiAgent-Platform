"""Keys-context domain dataclasses (D.4).

Plain records — no SQLAlchemy, no Pydantic, no framework types. Application
services pass these across the domain→infrastructure boundary; routers
translate to/from pydantic schemas at the edge.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from contexts.keys.domain.providers import ApiKeyProvider
from contexts.keys.infrastructure.probes.base import ProbeStatus


@dataclass(frozen=True, slots=True)
class ApiKey:
    """An Individual's provider key — plaintext is never held by this object."""

    id: uuid.UUID
    owner_user_id: uuid.UUID
    provider: ApiKeyProvider
    name: str
    masked_preview: str
    test_status: ProbeStatus
    test_error: str | None
    last_test_at: datetime | None
    transit_key_version: int
    hmac_key_version: int
    created_at: datetime
    deleted_at: datetime | None


def mask_preview(plaintext: str) -> str:
    """Build the non-reversible preview stored in `api_keys.masked_preview`.

    Matches the §7.2 example (``"sk-ant-...xE9a"``): keep enough prefix to
    identify the provider scheme and four trailing characters so a user can
    distinguish duplicates they own. Shorter inputs collapse to asterisks.
    """
    s = plaintext.strip()
    if len(s) < 12:
        return "***" + (s[-4:] if len(s) >= 4 else "")
    return f"{s[:7]}...{s[-4:]}"


__all__ = ["ApiKey", "mask_preview"]
