"""Transport-independent dashboard read models."""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from dataclasses import dataclass
from typing import Literal


class TrafficLight(str, enum.Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    INACTIVE = "inactive"


class RoundState(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"


class DashboardSubjectKind(str, enum.Enum):
    USER = "user"
    GUEST = "guest"
    MEMBER_GROUP = "member_group"


@dataclass(frozen=True, slots=True)
class DashboardActivation:
    activation_id: uuid.UUID
    is_active: bool
    started_at: dt.datetime
    ended_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class DashboardRoundAggregate:
    activation_id: uuid.UUID
    total_submissions: int
    valid_count: int
    last_valid_submission_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class DashboardRoundSubject:
    subject_id: uuid.UUID
    kind: DashboardSubjectKind


@dataclass(frozen=True, slots=True)
class DashboardWatchlistCandidate:
    room_id: uuid.UUID
    subject_id: uuid.UUID
    kind: DashboardSubjectKind
    valid_count: int
    last_valid_submission_at: dt.datetime | None
    needs_attention: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class DashboardThresholds:
    green_minutes: int
    yellow_minutes: int
    idle_minutes: int


@dataclass(frozen=True, slots=True)
class DashboardRound:
    activation_id: uuid.UUID
    state: RoundState
    started_at: dt.datetime
    ended_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class DashboardMessageCounts:
    human: int
    agent: int


@dataclass(frozen=True, slots=True)
class DashboardRoom:
    room_id: uuid.UUID
    room_name: str
    round: DashboardRound | None
    total_submissions: int
    valid_count: int
    last_valid_submission_at: dt.datetime | None
    status: TrafficLight
    online_count: int | None
    participant_count: int
    message_counts: DashboardMessageCounts


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    rooms: list[DashboardRoom]
    thresholds: DashboardThresholds
    generated_at: dt.datetime


@dataclass(frozen=True, slots=True)
class DashboardTimeseriesBucket:
    room_id: uuid.UUID
    bucket: dt.datetime
    count: int


@dataclass(frozen=True, slots=True)
class DashboardWatchlistEntry:
    room_id: uuid.UUID
    kind: Literal["user", "guest", "group"]
    display_name: str | None
    subject_code: str
    valid_count: int
    last_valid_submission_at: dt.datetime | None
    needs_attention: bool
    reason: Literal["no_valid_in_round", "idle"] | None


@dataclass(frozen=True, slots=True)
class DashboardWatchlist:
    entries: list[DashboardWatchlistEntry]
