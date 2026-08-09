"""Structured-activities domain models (Chapter §30, R30.01-R30.11).

Transport- and framework-agnostic read models mirroring the DB rows, so the
service/facade never leak ORM types to the web/worker layers. Three nouns:

- :class:`ActivityType`       — a registered type (payload schema + validator
                                config), owned by a project or by the platform
                                (:class:`ActivityTypeScope`).
- :class:`ActivitySession`    — a subject's run of a type in a room; carries the
                                server-assigned monotonic ``attempt_no`` counter.
- :class:`ActivitySubmission` — the authoritative record of one scored submission.

:class:`ValidationResult` is the value object a validator returns; the service maps
it onto the submission row (it is never persisted verbatim).
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from dataclasses import dataclass, field
from typing import Any


class ValidatorKind(str, enum.Enum):
    IN_PROCESS = "in_process"
    MCP = "mcp"
    WEBHOOK = "webhook"


class SessionStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class ActivationStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"


class ValidationStatus(str, enum.Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    ERROR = "error"


class ActivityTypeScope(str, enum.Enum):
    """Who owns an ``ActivityType`` ([R30.02]).

    ``PROJECT`` is the original and only pre-0076 case: the row belongs to one
    project and ``project_id`` is set. ``PLATFORM`` is a shipped example a
    platform admin installed: no owning project, reachable from a project only
    through a ``ProjectActivityTypeOptIn`` row ([R30.33]).

    Deliberately two values, mirroring ``ActivityPolicy.scope`` — a per-org layer
    would be a third value, which is a row-level concern rather than a rewrite.
    """

    PROJECT = "project"
    PLATFORM = "platform"


@dataclass(frozen=True, slots=True)
class ActivityType:
    id: uuid.UUID
    # None exactly when ``scope`` is PLATFORM; the pairing is enforced by the
    # ck_activity_types_project_scope CHECK, not only by this class.
    project_id: uuid.UUID | None
    key: str
    name: str
    payload_schema: dict[str, Any]
    validator_kind: ValidatorKind
    validator_config: dict[str, Any]
    # D-1 (research retention): NULL follows the room's normal purge; a positive
    # integer sets each submission's retain_until to created_at + retention_days.
    retention_days: int | None
    version: int
    created_at: dt.datetime
    # Presentation gates on the already-persisted ``agent_digest`` (never affect
    # what is stored, only what each channel is allowed to show).
    # expose_payload_to_agent is the master switch (no channel shows content
    # when false). echo_includes_content only takes effect when
    # expose_payload_to_agent is ALSO true: a chat message visible to humans is
    # necessarily visible to any agent reading that same transcript, so the two
    # cannot be fully independent (submission_service.py::submit's echo gate).
    expose_payload_to_agent: bool = True
    echo_includes_content: bool = False
    deleted_at: dt.datetime | None = None
    # Defaulted rather than positional so the ~150 existing construction sites
    # (tests included) keep describing the case they always described.
    scope: ActivityTypeScope = ActivityTypeScope.PROJECT

    def is_visible_to(self, project_id: uuid.UUID, *, opted_in: bool) -> bool:
        """Whether this type may be used from ``project_id`` ([R30.09], [R30.33]).

        The single expression of the tenancy rule that activation, session
        opening, and submission each used to re-type as ``project_id`` equality.
        Pure on purpose: ``opted_in`` is supplied by the caller, which is what
        keeps the rule in the domain while the opt-in lookup stays in the
        application layer (see ``application/reachability.py``).

        A platform type is never visible on ``project_id`` alone — the opt-in row
        is the authorization record, so a caller that cannot answer ``opted_in``
        cannot accidentally get a permissive answer out of this.
        """
        if self.scope is ActivityTypeScope.PLATFORM:
            return opted_in
        return self.project_id == project_id


@dataclass(frozen=True, slots=True)
class ProjectActivityTypeOptIn:
    """One project's opt-in to one platform-scoped type ([R30.33]).

    The row *is* the authorization: it is checked server-side on every room-level
    path, not merely used to filter the picker.
    """

    project_id: uuid.UUID
    activity_type_id: uuid.UUID
    enabled_by_user_id: uuid.UUID | None
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class ActivityPolicy:
    """Platform-wide governance over an ActivityType's three privacy- and
    retention-grade fields ([R30.29]).

    For each boolean: a ``default`` that pre-fills the authoring form, and a
    ``locked`` flag that additionally forces a type to match it. For retention a
    lock would be meaninglessly rigid, so it carries an upper bound instead;
    ``retention_days_max`` of ``None`` means unbounded.

    ``scope`` exists with only one legal value in v1. The column is here so a
    per-org layer is a later row rather than a migration rewrite — the shape
    follows ``prompt_assistant_configs``, which resolves user -> org -> platform.
    """

    id: uuid.UUID | None
    scope: str
    expose_payload_to_agent_default: bool
    expose_payload_to_agent_locked: bool
    echo_includes_content_default: bool
    echo_includes_content_locked: bool
    retention_days_default: int | None
    retention_days_max: int | None
    version: int
    updated_at: dt.datetime | None = None
    updated_by_user_id: uuid.UUID | None = None


PLATFORM_SCOPE = "platform"

# What the platform behaves as before an admin has ever saved a policy ([R30.29]):
# nothing locked, no retention ceiling, and defaults matching the ActivityType
# column defaults. Installing the capability must change no existing behavior, so
# these are permissive by construction — an admin has to choose to tighten.
PERMISSIVE_POLICY = ActivityPolicy(
    id=None,
    scope=PLATFORM_SCOPE,
    expose_payload_to_agent_default=True,
    expose_payload_to_agent_locked=False,
    echo_includes_content_default=False,
    echo_includes_content_locked=False,
    retention_days_default=None,
    retention_days_max=None,
    version=0,
)


@dataclass(frozen=True, slots=True)
class ActivitySession:
    id: uuid.UUID
    activity_type_id: uuid.UUID
    chatroom_id: uuid.UUID
    subject_user_id: uuid.UUID
    status: SessionStatus
    created_at: dt.datetime
    closed_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ActivityActivation:
    id: uuid.UUID
    chatroom_id: uuid.UUID
    activity_type_id: uuid.UUID
    started_by_user_id: uuid.UUID
    status: ActivationStatus
    created_at: dt.datetime
    ended_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ActivityActivationEndResult:
    """The persisted activation and whether this request ended it."""

    activation: ActivityActivation
    transitioned: bool


@dataclass(frozen=True, slots=True)
class ActivitySubmission:
    id: uuid.UUID
    session_id: uuid.UUID
    activity_type_id: uuid.UUID
    chatroom_id: uuid.UUID
    producer_user_id: uuid.UUID
    payload: dict[str, Any]
    attempt_no: int
    validation_status: ValidationStatus
    is_valid: bool | None
    error_class: str | None
    sub_scores: dict[str, Any]
    latency_ms: int | None
    retain_until: dt.datetime | None
    created_at: dt.datetime
    # Always computed at submit (payload-JSON fallback) and refined once a
    # validator supplies ``ValidationResult.detail``; presentation is gated by
    # the type's ``expose_payload_to_agent``/``echo_includes_content``, not here.
    agent_digest: str | None = None
    validated_at: dt.datetime | None = None
    deleted_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """A validator's verdict for one submission (not persisted verbatim)."""

    is_valid: bool
    error_class: str | None = None
    sub_scores: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RecentActivityRow:
    """A denormalized recent-submission row for the observer context provider
    (R30.10): the deterministic facts a diagnostic agent reads, joined from
    submission + session + type."""

    created_at: dt.datetime
    subject_user_id: uuid.UUID
    attempt_no: int
    type_key: str
    validation_status: ValidationStatus
    is_valid: bool | None
    error_class: str | None
    agent_digest: str | None = None
    expose_payload_to_agent: bool = True


@dataclass(frozen=True, slots=True)
class PolicyImpact:
    """What a candidate policy would block.

    ``violating_types`` counts live types it would refuse to activate.
    ``violating_activations`` counts activities running *right now* whose type
    it would refuse: enforcement happens at authoring and at activation start,
    so tightening a policy does not stop a class already under way, and an admin
    tightening for a consent reason needs to see that before saving.

    ``approximate`` is true when either scan hit its bound, so the counts are
    floors rather than silently truncated totals — a governance preview that
    quietly under-reported would be worse than none.
    """

    violating_types: int
    violating_activations: int
    approximate: bool


@dataclass(frozen=True, slots=True)
class ActivityAggregate:
    """Per subject/session/room read model (R30.10): counts, error-class
    distribution, and latency statistics from a single grouped query."""

    total: int
    valid_count: int
    error_count: int
    pending_count: int
    error_class_histogram: dict[str, int]
    latency_avg_ms: float | None
    latency_min_ms: int | None
    latency_max_ms: int | None


__all__ = [
    "PERMISSIVE_POLICY",
    "PLATFORM_SCOPE",
    "ActivityAggregate",
    "ActivityActivation",
    "ActivityActivationEndResult",
    "ActivityPolicy",
    "ActivitySession",
    "ActivitySubmission",
    "ActivityType",
    "ActivityTypeScope",
    "ActivationStatus",
    "PolicyImpact",
    "ProjectActivityTypeOptIn",
    "RecentActivityRow",
    "SessionStatus",
    "ValidationResult",
    "ValidationStatus",
    "ValidatorKind",
]
