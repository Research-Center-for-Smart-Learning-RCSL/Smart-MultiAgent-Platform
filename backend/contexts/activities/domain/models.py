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


class SubjectKind(str, enum.Enum):
    """What kind of thing an :class:`ActivitySession` belongs to ([R30.39]).

    Not a column: it is derived from which of the two subject fields is set, and
    the database CHECK (0081) guarantees exactly one of them is. Deriving rather
    than storing means the two can never disagree.
    """

    USER = "user"
    MEMBER_GROUP = "member_group"


class ProposalStatus(str, enum.Enum):
    """A group proposal's lifecycle ([R30.41]).

    ``OPEN`` is the only non-terminal value; once a proposal leaves it, it can
    never produce a submission. ``REJECTED`` means the threshold became
    unreachable, which is not the same as "somebody voted no" unless the
    configured fraction makes it so (``domain/group_consent.py``).
    """

    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class VoteChoice(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"


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
    # The consent fraction this type requires of a group ([R30.40]); ``None``
    # means individual-only, which is every type that predates 0081. Shape and
    # arithmetic live in ``domain/group_consent.py`` -- deliberately NOT in
    # ``validator_config``, which is owner-confidential ([R30.25]) while the
    # people voting must be able to see the bar they are voting against.
    group_config: dict[str, Any] | None = None

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
    """A subject's run of a type in one round; the subject is a user or a group.

    ``subject_user_id`` and ``subject_member_group_id`` are polymorphic and
    mutually exclusive ([R30.39]): exactly one is set, enforced by the database
    CHECK ``ck_activity_sessions_one_subject`` (0081) rather than by convention
    here. Read the pair through :attr:`subject_kind`, which cannot be made to
    disagree with them.
    """

    id: uuid.UUID
    activity_type_id: uuid.UUID
    chatroom_id: uuid.UUID
    # Optional since 0081. ``None`` exactly when this is a group session -- never
    # "unknown", which the CHECK makes unrepresentable.
    subject_user_id: uuid.UUID | None
    status: SessionStatus
    created_at: dt.datetime
    closed_at: dt.datetime | None = None
    # The round this session was answered under (0077). Defaulted rather than
    # positional so the existing construction sites keep describing the case they
    # always described; ``None`` means a pre-0077 row, never a live one.
    activation_id: uuid.UUID | None = None
    # The subject's own "I am finished" declaration, independent of ``status``:
    # the participant sets and clears this, the facilitator's end-of-round sets
    # ``status``. A submission clears it (submission_service.py::submit).
    completed_at: dt.datetime | None = None
    # Set exactly when ``subject_user_id`` is not (0081). Defaulted rather than
    # positional so the existing construction sites keep describing the personal
    # case they always described.
    subject_member_group_id: uuid.UUID | None = None

    @property
    def subject_kind(self) -> SubjectKind:
        """Whether this session belongs to a person or to a group."""
        return SubjectKind.MEMBER_GROUP if self.subject_member_group_id is not None else SubjectKind.USER


@dataclass(frozen=True, slots=True)
class ActivityActivation:
    id: uuid.UUID
    chatroom_id: uuid.UUID
    activity_type_id: uuid.UUID
    # Always a user, even for a delegated round ([R30.37]): an agent acts on a
    # granting teacher's authority, and that teacher is both the answerable party
    # and the recipient the per-round progress event is addressed to.
    started_by_user_id: uuid.UUID
    status: ActivationStatus
    created_at: dt.datetime
    ended_at: dt.datetime | None = None
    # The agent that called `start_activity`, or None for a human-started round.
    # A bare id: this context may not import the agents context ([R30.05]), so it
    # resolves to a name only at the route.
    started_by_agent_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ActivityActivationEndResult:
    """The persisted activation and whether this request ended it."""

    activation: ActivityActivation
    transitioned: bool


@dataclass(frozen=True, slots=True)
class ActivitySessionCompletionResult:
    """The outcome of a participant's "I am finished" toggle.

    Carries the ``activation`` so the route can address its post-commit broadcast
    at the facilitator who started the round without a second lookup, and
    ``transitioned`` so a repeat click does not replay an event claiming the
    count moved (the shape :class:`ActivityActivationEndResult` already uses).
    """

    session: ActivitySession
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
class ProposalVote:
    """One pinned voter's decision on one proposal ([R30.42]).

    Readable only by the proposal's pinned voters and the room creator, and never
    by any agent: the record names people and records dissent, which is an
    accountability record for the group and the teacher rather than class
    material.
    """

    proposal_id: uuid.UUID
    user_id: uuid.UUID
    choice: VoteChoice
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class GroupProposal:
    """A payload one group member proposes and the group votes on ([R30.41]).

    ``voter_user_ids`` and ``required_approvals`` are BOTH pinned at creation and
    stored. A person added to the group mid-vote cannot be bound by a proposal
    they never saw, and a person removed does not lower a bar the group already
    agreed to clear -- so resolution reads neither the membership nor the
    fraction again.

    ``payload`` is the proposer's own text. The other members approved it; they
    did not write it, which is why the resulting submission records the proposer
    as its ``producer_user_id``.
    """

    id: uuid.UUID
    chatroom_id: uuid.UUID
    activation_id: uuid.UUID
    activity_type_id: uuid.UUID
    member_group_id: uuid.UUID
    proposer_user_id: uuid.UUID
    payload: dict[str, Any]
    voter_user_ids: tuple[uuid.UUID, ...]
    required_approvals: int
    status: ProposalStatus
    created_at: dt.datetime
    expires_at: dt.datetime
    resolved_at: dt.datetime | None = None
    submission_id: uuid.UUID | None = None

    def may_vote(self, user_id: uuid.UUID) -> bool:
        """Whether this user holds a ballot. Membership of the group today is not
        the question -- the pin is ([R30.41])."""
        return user_id in self.voter_user_ids


@dataclass(frozen=True, slots=True)
class GroupProposalTally:
    """A proposal plus its vote counts, and the votes themselves.

    ``votes`` is empty for a caller who may not see them, so one read model
    serves both the counts (which the room may learn) and the per-person record
    (which only the pinned voters and the room creator may) without a second
    shape that could drift from this one.
    """

    proposal: GroupProposal
    approvals: int
    rejections: int
    undecided: int
    votes: tuple[ProposalVote, ...] = ()


@dataclass(frozen=True, slots=True)
class GroupProposalResolution:
    """What resolving a proposal produced.

    ``submission`` is set only on acceptance; ``transitioned`` is false when the
    call found the proposal already resolved, so a repeat vote does not replay a
    broadcast claiming the count moved (the shape
    :class:`ActivityActivationEndResult` already uses).
    """

    tally: GroupProposalTally
    transitioned: bool
    submission: ActivitySubmission | None = None
    signal_payload: dict[str, Any] | None = None


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
    # Polymorphic with ``subject_member_group_id`` below, exactly as the session
    # row it is joined from ([R30.39]): ``None`` here means the row belongs to a
    # group, never that the subject is unknown.
    subject_user_id: uuid.UUID | None
    attempt_no: int
    type_key: str
    validation_status: ValidationStatus
    is_valid: bool | None
    error_class: str | None
    agent_digest: str | None = None
    expose_payload_to_agent: bool = True
    # Whether ``agent_digest`` came from a validator's ``detail`` rather than from
    # the payload-dump fallback. Load-bearing for the context block, which tells
    # the model that a row's trailing text is the participant's own words: once a
    # type adopts a validator that describes the submission instead of quoting it,
    # that promise would be false for those rows. Derived at read time rather than
    # stored, so it is also correct for every row written before the distinction
    # existed. Carries no submission content either way.
    digest_is_computed: bool = False
    # Set exactly when ``subject_user_id`` is not. Defaulted so every existing
    # construction site keeps describing a personal row.
    subject_member_group_id: uuid.UUID | None = None

    @property
    def subject_kind(self) -> SubjectKind:
        return SubjectKind.MEMBER_GROUP if self.subject_member_group_id is not None else SubjectKind.USER


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


#: ``sub_scores`` key under which a validator records **which** declared properties
#: a submission answered, as a JSON array of property names ([R28.17]). Declared
#: here rather than beside the validator that writes it: the coverage aggregates
#: read the key in SQL, and ``contexts/activities`` must not import ``app.plugins``.
#: A submission without it has no coverage to report — which is the mid-course
#: upgrade case, not an error.
FILLED_FIELDS_SUB_SCORE = "filled_fields"

#: Upper bound on the declared properties a coverage aggregate will tally. The
#: query builds one aggregate per field, so an unbounded schema would build an
#: unbounded statement. Well above any worksheet: the shipped nine-cell mandala is
#: the widest example type, and a form with more boxes than this is not something a
#: single figure could render legibly anyway.
MAX_COVERAGE_FIELDS = 64


@dataclass(frozen=True, slots=True)
class FieldCoverageCell:
    """One declared property and how many counted submissions answered it.

    ``title`` is the owner-authored schema title, falling back to the property
    name. Both are owner-authored; **no participant value is carried here** —
    the aggregate that builds these reads field *names* only ([R28.18]).
    """

    name: str
    title: str
    filled: int


@dataclass(frozen=True, slots=True)
class FieldCoverage:
    """Per-field answer counts for one activity type in one room ([R28.17]).

    ``submissions_counted`` is the denominator and it counts **submissions, not
    participants**: a coverage figure over this data cannot say what fraction of a
    class did anything, because only submissions carrying ``filled_fields`` are in
    scope at all and the room has no roster ([R28.18]).

    ``cells`` is in the schema's declared order (``x-order`` where present).
    """

    type_key: str
    type_name: str
    submissions_counted: int
    cells: tuple[FieldCoverageCell, ...]


@dataclass(frozen=True, slots=True)
class MandalaGrid:
    """:class:`FieldCoverage` for a nine-property type, laid out three by three.

    A separate read model rather than a flag on ``FieldCoverage`` because the
    nine-property requirement is an invariant of *this* shape: ``rows`` is always
    three rows of three, so a renderer never has to handle a ragged grid.
    """

    type_key: str
    type_name: str
    submissions_counted: int
    rows: tuple[tuple[FieldCoverageCell, ...], ...]


@dataclass(frozen=True, slots=True)
class AttemptSummaryRow:
    """One participant's attempt record, addressed by truncated code ([R28.18]).

    ``attempts`` is the highest attempt number reached within a **single** session,
    not a total across sessions: attempt numbers are per session, so a participant
    who tried twice in each of two rounds reports 2, not 4.
    """

    subject_code: str
    attempts: int
    submissions: int
    latest_outcome: str
    latest_error_class: str | None


@dataclass(frozen=True, slots=True)
class AttemptSummary:
    """Room-scoped attempt records, newest activity first ([R28.17]).

    ``truncated`` says the limit cut the listing short. Reported rather than
    silently dropped: a table that stops at its cap with no sign of it reads as a
    complete record of the room, which is the one thing this data is not.
    """

    type_key: str | None
    type_name: str | None
    submissions_counted: int
    rows: tuple[AttemptSummaryRow, ...]
    truncated: bool


__all__ = [
    "FILLED_FIELDS_SUB_SCORE",
    "MAX_COVERAGE_FIELDS",
    "PERMISSIVE_POLICY",
    "PLATFORM_SCOPE",
    "ActivationStatus",
    "ActivityActivation",
    "ActivityActivationEndResult",
    "ActivityAggregate",
    "ActivityPolicy",
    "ActivitySession",
    "ActivitySessionCompletionResult",
    "ActivitySubmission",
    "ActivityType",
    "ActivityTypeScope",
    "AttemptSummary",
    "AttemptSummaryRow",
    "FieldCoverage",
    "FieldCoverageCell",
    "GroupProposal",
    "GroupProposalResolution",
    "GroupProposalTally",
    "MandalaGrid",
    "PolicyImpact",
    "ProjectActivityTypeOptIn",
    "ProposalStatus",
    "ProposalVote",
    "RecentActivityRow",
    "SessionStatus",
    "SubjectKind",
    "ValidationResult",
    "ValidationStatus",
    "ValidatorKind",
    "VoteChoice",
]
