"""Conversation domain dataclasses — framework-free."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


class SenderType(str, enum.Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    GUEST = "guest"


# `metadata.type` of the system row a room release writes ([R28.06]).
RELEASED_OBSERVATION_TYPE = "released_observation"


class ExportSenderScope(str, enum.Enum):
    """How much of a readable room a chat export may carry (matrix row 19).

    ALL                — every sender, for owners and admins (the `✓` cells).
    OWN_PLUS_NON_USER  — the caller's own messages plus all agent and system
                         messages, for members (the `∘` cells). Other users' and
                         guests' messages, edit histories and attachments are
                         excluded. (The value predates the guest sender type and
                         is persisted in job records, so it keeps its name.)

    Ordering matters: ALL is strictly wider than OWN_PLUS_NON_USER, and the
    export worker refuses to widen past the scope recorded at request time.
    """

    ALL = "all"
    OWN_PLUS_NON_USER = "own_plus_non_user"


class ChatroomAgentRole(str, enum.Enum):
    NORMAL = "normal"
    OBSERVER = "observer"


class ClassroomRole(str, enum.Enum):
    """A binding's part in the classroom agent design ([R34.01]).

    Independent of :class:`ChatroomAgentRole`, which decides output routing:
    ``teacher`` and ``peer`` sit on normal bindings, ``analyst`` on an observer.
    """

    NONE = "none"
    TEACHER = "teacher"
    PEER = "peer"
    ANALYST = "analyst"

    def fits(self, role: ChatroomAgentRole) -> bool:
        if self is ClassroomRole.NONE:
            return True
        if self is ClassroomRole.ANALYST:
            return role is ChatroomAgentRole.OBSERVER
        return role is ChatroomAgentRole.NORMAL


class AlertViewSurface(str, enum.Enum):
    PANEL = "panel"
    DASHBOARD = "dashboard"


class AlertActionKind(str, enum.Enum):
    """The room creator's first response to an alert ([R34.02])."""

    RELEASED = "released"
    DELETED = "deleted"
    ROOM_MESSAGE = "room_message"
    ACTIVITY_STARTED = "activity_started"
    ACTIVITY_ENDED = "activity_ended"


#: [R34.02]: matches ck_agent_observations_alert_reason (0110).
ALERT_REASON_MAX_CHARS = 200


class NoticeErrorKind(str, enum.Enum):
    """What an analyst notice is about ([R34.04])."""

    CONTENT = "content"
    STRATEGY = "strategy"


class NoticeDecision(str, enum.Enum):
    """The teacher agent's reaction to a notice ([R34.05])."""

    ACCEPTED = "accepted"
    PARTLY_ACCEPTED = "partly_accepted"
    DECLINED = "declined"


class NoticeStatus(str, enum.Enum):
    """Derived, never stored: see :meth:`AnalystNotice.status_at`."""

    PENDING = "pending"
    ANSWERED = "answered"
    UNANSWERED = "unanswered"


#: [R34.04], [R34.05]: match the CHECK constraints of 0111.
NOTICE_TEXT_MAX_CHARS = 1000
NOTICE_REASON_MAX_CHARS = 500
#: [R34.05]: a notice nobody answered within this window reads as unanswered.
NOTICE_ANSWER_WINDOW = timedelta(minutes=15)
#: [R34.04]: at most this many notices per room within ``NOTICE_RATE_WINDOW``.
NOTICE_RATE_LIMIT = 3
NOTICE_RATE_WINDOW = timedelta(minutes=10)


class PeerReviewStatus(str, enum.Enum):
    """A held peer reply's state ([R34.06]). ``pending`` past ``due_at`` reads as
    ``expired``: see :meth:`PeerReplyReview.status_at`."""

    PENDING = "pending"
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PeerReviewDecision(str, enum.Enum):
    """What the teacher agent's ``review_peer_reply`` call may decide."""

    APPROVE = "approve"
    MODIFY = "modify"
    REJECT = "reject"
    DEFER = "defer"


class PeerReviewExpiry(str, enum.Enum):
    """Why a review expired unposted. Recorded for the research export."""

    NO_TEACHER = "no_teacher"
    NO_DECISION = "no_decision"
    BINDING_CHANGED = "binding_changed"
    DEFER_LIMIT = "defer_limit"
    FAILED = "failed"


#: [R34.06]: the review turn's deadline, and the slack before a pending review
#: whose job died reads as expired.
PEER_REVIEW_DEADLINE = timedelta(seconds=60)
PEER_REVIEW_GRACE = timedelta(seconds=90)
#: Attempt 1 is the first review; each defer adds one, and deferring attempt 3 expires.
PEER_REVIEW_MAX_ATTEMPTS = 3
PEER_REVIEW_DEFER_MINUTES = (1, 10)
PEER_REVIEW_REASON_MAX_CHARS = 500
PEER_REVIEW_REVISED_MAX_CHARS = 20_000
#: Rejections told per peer turn, bounding its prompt; the rest follow on later turns.
PEER_REVIEW_TOLD_PER_TURN = 5


class ChatroomAgentBubbleColor(str, enum.Enum):
    CYAN = "cyan"
    TEAL = "teal"
    GREEN = "green"
    LIME = "lime"
    AMBER = "amber"
    ORANGE = "orange"
    ROSE = "rose"
    VIOLET = "violet"


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
    # §32 ([R32.05]). True by default, matching `disclose_observers` and the column:
    # a room that has never been configured tells its participants.
    disclose_drafts: bool = True
    # [R15.30]: while set, no automatic trigger runs a turn in this room.
    class_closed_at: datetime | None = None


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
    # Live draft reading ([R32.03]). A second, independent authority sharing one
    # grantor column — a binding may hold either, both, or neither, and the two are
    # written by different routes. Never read one to infer the other.
    may_read_drafts: bool = False
    may_read_canvas: bool = False
    may_write_canvas: bool = False
    # The same row's wake-up layers, when the read that built this selected them,
    # so a message's wake-up evaluation needs no second read of the room's rows.
    wakeup: BindingWakeupState | None = None
    bubble_color: ChatroomAgentBubbleColor | None = None
    classroom_role: ClassroomRole = ClassroomRole.NONE


@dataclass(frozen=True, slots=True)
class BindingWakeupState:
    """One live binding's stored wake-up layers ([R15.09]).

    The layers stay raw here: their meaning is orchestration's
    (``orchestration.domain.room_wakeup``), and conversation only stores them.
    """

    chatroom_id: uuid.UUID
    agent_id: uuid.UUID
    binding_id: uuid.UUID
    role: ChatroomAgentRole
    version: int
    room_overrides: dict[str, Any]
    runtime_overrides: dict[str, Any]
    runtime_started_at: datetime | None


@dataclass(frozen=True, slots=True)
class CanvasReadGrant:
    """A live delegation of canvas-content reading in one room ([R13.47]).

    Same shape as :class:`DraftReadGrant`: holding one *is* the authorization.
    """

    agent_id: uuid.UUID
    granted_by_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class CanvasWriteGrant:
    """A live delegation of canvas-object writing in one room ([R13.56])."""

    agent_id: uuid.UUID
    granted_by_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class DraftReadGrant:
    """A live delegation of unsent-text reading in one room ([R32.03]).

    The same shape as :class:`ActivityControlGrant` and for the same reason: it is
    only ever constructed for a binding whose ``may_read_drafts`` is true and whose
    grantor is still on record, so holding one *is* the authorization.

    It carries no type allowlist. What an agent may read is decided at read time by
    the activity type's own ``expose_payload_to_agent`` and by the platform payload
    policy ([R32.04]) — the same two gates a *submitted* payload passes — rather
    than by a second list the teacher would have to keep in step with the first. A
    draft is therefore never readable on looser terms than the submission it is
    becoming, which is the rule this whole feature rests on.
    """

    agent_id: uuid.UUID
    granted_by_user_id: uuid.UUID


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
    # [R28.15] — ordered presentation blocks; empty for every observation recorded
    # before the feature and for any turn that did not call `present_observation`.
    # `content_md` is their serialisation, stored rather than derived, so an
    # observation stays readable even if the block schema is rolled back.
    blocks: list[dict[str, Any]] = field(default_factory=list)
    trigger_message_id: uuid.UUID | None = None
    released_at: datetime | None = None
    release_target: dict[str, Any] | None = None
    released_by_user_id: uuid.UUID | None = None
    created_at: datetime | None = None
    deleted_at: datetime | None = None
    # [R34.02]. Every "first" field is written once, by the earliest report.
    is_alert: bool = False
    alert_reason: str | None = None
    first_viewed_at: datetime | None = None
    first_viewed_via: AlertViewSurface | None = None
    first_action_at: datetime | None = None
    first_action_kind: AlertActionKind | None = None


@dataclass(frozen=True, slots=True)
class AnalystNotice:
    """An analyst agent's private note to the room's teacher agent ([R34.04])."""

    id: uuid.UUID
    chatroom_id: uuid.UUID
    analyst_agent_id: uuid.UUID
    analyst_binding_id: uuid.UUID
    teacher_agent_id: uuid.UUID
    teacher_binding_id: uuid.UUID
    error_kind: NoticeErrorKind
    error: str
    suggestion: str
    created_at: datetime
    about_message_id: uuid.UUID | None = None
    decision: NoticeDecision | None = None
    reason: str | None = None
    responded_at: datetime | None = None

    def status_at(self, now: datetime) -> NoticeStatus:
        if self.decision is not None:
            return NoticeStatus.ANSWERED
        if now - self.created_at >= NOTICE_ANSWER_WINDOW:
            return NoticeStatus.UNANSWERED
        return NoticeStatus.PENDING


@dataclass(frozen=True, slots=True)
class PeerReplyReview:
    """A peer agent's reply held for the teacher agent's decision ([R34.06])."""

    id: uuid.UUID
    chatroom_id: uuid.UUID
    peer_agent_id: uuid.UUID
    peer_binding_id: uuid.UUID
    draft: str
    status: PeerReviewStatus
    attempt: int
    due_at: datetime
    created_at: datetime
    teacher_agent_id: uuid.UUID | None = None
    teacher_binding_id: uuid.UUID | None = None
    trigger_message_id: uuid.UUID | None = None
    reason: str | None = None
    revised_text: str | None = None
    expire_reason: PeerReviewExpiry | None = None
    decided_at: datetime | None = None
    posted_message_id: uuid.UUID | None = None
    #: The turn's reply metadata (RAG sources, skill reads, synthesis markers),
    #: carried onto the posted message so a held reply loses none of it.
    reply_metadata: dict[str, Any] = field(default_factory=dict)

    def status_at(self, now: datetime) -> PeerReviewStatus:
        if self.status is PeerReviewStatus.PENDING and now >= self.due_at:
            return PeerReviewStatus.EXPIRED
        return self.status


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


@dataclass(frozen=True, slots=True)
class GuestSession:
    id: uuid.UUID
    chatroom_id: uuid.UUID
    display_name: str
    browser_id: str | None
    refresh_token_hash: str
    last_seen_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GuestBan:
    id: uuid.UUID
    chatroom_id: uuid.UUID
    guest_session_id: uuid.UUID
    display_name: str
    created_by: uuid.UUID | None
    created_at: datetime


CLASS_START_MIN_PARTICIPANTS_RANGE = (1, 100)
CLASS_START_MAX_WAIT_MINUTES_RANGE = (1, 60)


def _bounded(value: Any, bounds: tuple[int, int]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if bounds[0] <= value <= bounds[1] else None


@dataclass(frozen=True, slots=True)
class ClassStartConfig:
    """When a room's class opens without its creator ([R15.27])."""

    enabled: bool
    min_participants: int
    max_wait_minutes: int

    @classmethod
    def from_stored(cls, raw: Any) -> ClassStartConfig | None:
        """Tolerant parse: a malformed stored value is "off", never a guessed threshold."""
        if not isinstance(raw, dict) or not isinstance(raw.get("enabled"), bool):
            return None
        minimum = _bounded(raw.get("min_participants"), CLASS_START_MIN_PARTICIPANTS_RANGE)
        wait = _bounded(raw.get("max_wait_minutes"), CLASS_START_MAX_WAIT_MINUTES_RANGE)
        if minimum is None or wait is None:
            return None
        return cls(enabled=raw["enabled"], min_participants=minimum, max_wait_minutes=wait)

    def to_stored(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "min_participants": self.min_participants,
            "max_wait_minutes": self.max_wait_minutes,
        }

    @classmethod
    def patched(
        cls,
        base: ClassStartConfig | None,
        *,
        enabled: bool | None,
        min_participants: int | None,
        max_wait_minutes: int | None,
    ) -> ClassStartConfig | None:
        """``base`` with each given field replaced; ``None`` when a field is still missing,
        which is only possible for a room never configured."""
        merged_enabled = enabled if enabled is not None else (base.enabled if base else None)
        minimum = (
            min_participants if min_participants is not None else (base.min_participants if base else None)
        )
        wait = max_wait_minutes if max_wait_minutes is not None else (base.max_wait_minutes if base else None)
        if merged_enabled is None or minimum is None or wait is None:
            return None
        return cls(enabled=merged_enabled, min_participants=minimum, max_wait_minutes=wait)


@dataclass(frozen=True, slots=True)
class ClassStartState:
    chatroom_id: uuid.UUID
    config: ClassStartConfig | None
    first_arrival_at: datetime | None
    started_at: datetime | None
    closed_at: datetime | None = None

    @property
    def armed(self) -> bool:
        """Configured, switched on, and neither started nor closed in this class ([R15.30])."""
        return (
            self.config is not None
            and self.config.enabled
            and self.started_at is None
            and self.closed_at is None
        )


__all__ = [
    "CLASS_START_MAX_WAIT_MINUTES_RANGE",
    "CLASS_START_MIN_PARTICIPANTS_RANGE",
    "ActivityControlGrant",
    "AgentObservation",
    "AttachmentExtractionStatus",
    "AttachmentStatus",
    "BindingWakeupState",
    "Chatroom",
    "ChatroomAgent",
    "ChatroomAgentBubbleColor",
    "ChatroomAgentRole",
    "ChatroomGuest",
    "ClassStartConfig",
    "ClassStartState",
    "DraftReadGrant",
    "GuestBan",
    "GuestSession",
    "Message",
    "MessageAttachment",
    "MessageEdit",
    "ScanStatus",
    "SenderType",
    "Workspace",
]
