"""Key Group domain dataclasses + rotation/backoff config value object (D.6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RotationPolicy:
    """Per-member rotation + backoff config (R7.07 defaults baked in).

    The defaults here mirror the migration's server_default for every column.
    The application uses these when it has to materialise a default in-memory
    (e.g. when the API layer returns a patch target); the DB never sees them
    because inserts rely on server_default.
    """

    rotate_on_error_codes: tuple[int, ...] = (429, 500, 502, 503)
    rotate_on_token_quota: bool = True
    retry_on_error: bool = True
    retry_initial_delay_ms: int = 500
    retry_multiplier: Decimal = Decimal("2.0")
    retry_max_delay_ms: int = 30_000
    retry_max: int = 3
    retry_jitter_pct: int = 20


@dataclass(frozen=True, slots=True)
class HourlyLimits:
    """Per-member hourly caps (R7.09). Any NULL ⇒ unlimited for that axis."""

    max_input_tokens_per_hour: int | None = None
    max_output_tokens_per_hour: int | None = None
    max_requests_per_hour: int | None = None


@dataclass(frozen=True, slots=True)
class KeyGroup:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    created_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class KeyGroupMember:
    group_id: uuid.UUID
    key_id: uuid.UUID
    priority: int
    rotation: RotationPolicy = field(default_factory=RotationPolicy)
    limits: HourlyLimits = field(default_factory=HourlyLimits)


__all__ = ["HourlyLimits", "KeyGroup", "KeyGroupMember", "RotationPolicy"]
