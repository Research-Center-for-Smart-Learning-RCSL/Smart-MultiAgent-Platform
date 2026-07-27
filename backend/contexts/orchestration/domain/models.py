"""Orchestration domain dataclasses — framework-free.

Covers the A2A envelope (R9.13), wake-up config shape (§15.1),
and instruct/sub-agent value objects used by G.1–G.8.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from shared_kernel.type_guards import is_plain_int

# ---------------------------------------------------------------------------
# A2A envelope (R9.13)
# ---------------------------------------------------------------------------


class A2AMessageType(str, enum.Enum):
    CALL = "call"
    REPLY = "reply"
    NOTIFY = "notify"
    INSTRUCT = "instruct"


# Hard cap on synchronous A2A call nesting (R9.15). A CALL runs the callee's
# turn inline in its consumer loop, so an unbounded A->B->C... chain pins one
# worker task per level; this bounds it. Mirrors the instruct depth machinery.
A2A_CALL_MAX_DEPTH: int = 8

# Hard cap on broadcast fan-out (R9.16). A broadcast writes to one inbox stream
# per a2a-enabled agent in the project; bound it so a large project cannot turn
# a single broadcast into an unbounded write/turn storm. Recipients past the cap
# are dropped with a logged warning rather than silently.
A2A_BROADCAST_MAX_RECIPIENTS: int = 100


@dataclass(frozen=True, slots=True)
class A2AEnvelope:
    """Wire format for inter-agent messages (R9.13).

    Serialised to JSON and pushed into a Redis Stream entry.
    """

    id: uuid.UUID
    from_agent: uuid.UUID | None
    to_agent: str  # uuid or "broadcast:workspace"
    workflow_run_id: uuid.UUID | None
    type: A2AMessageType
    payload: dict[str, Any]
    correlation_id: uuid.UUID
    created_at: datetime
    # Synchronous CALL chain tracking (R9.15). `call_depth` is the 1-based
    # nesting level; `call_path` is the ordered tuple of callee agent-id strings
    # on the current call stack, used to reject A->B->A cycles. Default 0/() for
    # non-CALL traffic and root (workflow-originated) calls.
    call_depth: int = 0
    call_path: tuple[str, ...] = ()
    # Trigger-causality chain (R14.07a / F-4). `trigger_path` is the ordered
    # tuple of workflow-id strings whose runs caused this envelope to be emitted;
    # `trigger_depth` is its length. Stamped by the workflow executors that emit
    # A2A traffic (agent_invocation, instruct) so the a2a_event trigger dispatch
    # can refuse to start a run for a workflow already on the chain, breaking the
    # self-amplifying cycle. Default 0/() for user-originated and non-workflow
    # traffic (a genuine trigger root).
    trigger_depth: int = 0
    trigger_path: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "from_agent": str(self.from_agent) if self.from_agent else None,
            "to_agent": self.to_agent,
            "workflow_run_id": str(self.workflow_run_id) if self.workflow_run_id else None,
            "type": self.type.value,
            "payload": self.payload,
            "correlation_id": str(self.correlation_id),
            "created_at": self.created_at.isoformat(),
            "call_depth": self.call_depth,
            "call_path": list(self.call_path),
            "trigger_depth": self.trigger_depth,
            "trigger_path": list(self.trigger_path),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> A2AEnvelope:
        return cls(
            id=uuid.UUID(data["id"]),
            from_agent=uuid.UUID(data["from_agent"]) if data.get("from_agent") else None,
            to_agent=data["to_agent"],
            workflow_run_id=uuid.UUID(data["workflow_run_id"]) if data.get("workflow_run_id") else None,
            type=A2AMessageType(data["type"]),
            payload=data.get("payload") or {},
            correlation_id=uuid.UUID(data["correlation_id"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            call_depth=int(data.get("call_depth", 0)),
            call_path=tuple(data.get("call_path") or ()),
            trigger_depth=int(data.get("trigger_depth", 0)),
            trigger_path=tuple(data.get("trigger_path") or ()),
        )


# ---------------------------------------------------------------------------
# Wake-up config shape (§15.1)
# ---------------------------------------------------------------------------

# Hard caps applied at parse time so they're enforced regardless of how the
# JSONB was written (designer UI, direct DB edit, migration, etc.).
AUTOSTOP_HARD_CAP: int = 100
N_MIN: int = 1
N_MAX: int = 1000
T_MINUTES_MIN: int = 1
T_MINUTES_MAX: int = 1440


def clamp(value: int, minimum: int, maximum: int) -> int:
    """Bound `value` to `[minimum, maximum]`. Exported (not `_`-prefixed) so
    `WakeupService`'s soft-bounds clamping can share this formula instead of
    reimplementing it (2026-07-28-wakeup-config-validation-and-patch-semantics F-7)."""
    return max(minimum, min(maximum, value))


def _default_below_one(value: int, *, default: int, maximum: int | None = None) -> int:
    if value < 1:
        return default
    return min(value, maximum) if maximum is not None else value


def _tolerant_int(value: Any) -> int:
    """Coerce a raw JSONB numeric field to ``int`` for ``clamp``/``_default_below_one``.

    Anything that is not a genuine ``int`` -- ``None``, a non-numeric string, a list,
    a dict, or (Q-7, 2026-07-27-wakeup-config-type-validation) a ``bool`` despite being
    an ``int`` subclass -- resolves to 0, a sentinel below every documented floor so the
    existing clamp/default helpers apply the same resolution they already use for an
    out-of-range value. ``int(True) == 1`` is in range and would otherwise silently
    become "wake on every message".
    """
    if is_plain_int(value):
        return value
    return 0


def _tolerant_soft_bound(value: Any, *, minimum: int, maximum: int) -> int | None:
    """Coerce an optional designer-set soft bound (R15.08) to an int within the hard
    range, or ``None`` if absent or wrong-typed. Clamping here — not only at
    ``_clamp_n``/``_clamp_t`` — guarantees those always return a value inside the
    advertised hard range even from an inverted or out-of-range soft bound (prior
    dossier's FU-7)."""
    if not is_plain_int(value):
        return None
    return clamp(value, minimum, maximum)


def _tolerant_dict(value: Any) -> dict[str, Any]:
    """Coerce a raw JSONB container field to `dict` for the `.get()` chain below.

    A truthy non-dict (a string, a list, ...) resolves to `{}` rather than being
    read via `.get()` directly, which would raise `AttributeError` -- the same
    resolution `soft_bounds` already had via its own `isinstance` guard
    (2026-07-28-wakeup-config-validation-and-patch-semantics F-1).
    """
    return value if isinstance(value, dict) else {}


def _tolerant_bool(value: Any) -> bool:
    """Coerce a raw JSONB boolean field to `bool`, defaulting wrong-typed input to
    `False` rather than through `bool(value)`'s truthiness coercion.

    `bool("false")` is `True` in Python -- a bare `bool(...)` on a wrong-typed
    `enabled`/`allow_self_open` value would silently enable what the caller meant
    to disable (F-2). Defaulting to `False` (never silently enable) mirrors
    `_tolerant_int`'s "resolve to the safe floor" contract.
    """
    return value if isinstance(value, bool) else False


@dataclass(frozen=True, slots=True)
class EveryNMessagesTrigger:
    enabled: bool = False
    n: int = 3


@dataclass(frozen=True, slots=True)
class SilenceMinutesTrigger:
    enabled: bool = False
    t_minutes: int = 2
    autostop_rounds: int = 5
    autostop_max_default: int = 100
    # O-3 (R28.12): observer bindings get their own, higher round cap so a
    # long agent-only exchange keeps being observed (autostop resets only on
    # user messages, which an unattended room may never see).
    observer_autostop_rounds: int = 50

    def autostop_limit_for(self, *, is_observer: bool) -> int:
        """The configured autostop round cap for a binding of this role
        (O-3/R28.12). Single source of truth so the worker gate and the
        domain evaluator can't diverge."""
        return self.observer_autostop_rounds if is_observer else self.autostop_rounds


@dataclass(frozen=True, slots=True)
class CallOnlyTrigger:
    enabled: bool = False


# R28.07: trigger kinds that represent an explicit call the room's own
# participants made -- a @mention or a creator's release-wake -- as opposed to
# an autonomous every_n_messages/silence_minutes round. Single source so the
# worker gate (app/workers/tasks/orchestration.py) and TurnEngine's own skip
# notices can't diverge on which kinds count as explicit, mirroring
# `autostop_limit_for` above for the identical anti-divergence reason.
EXPLICIT_TRIGGERS: Final[frozenset[str]] = frozenset({"mention", "release"})


@dataclass(frozen=True, slots=True)
class WakeupTriggers:
    every_n_messages: EveryNMessagesTrigger = field(default_factory=EveryNMessagesTrigger)
    silence_minutes: SilenceMinutesTrigger = field(default_factory=SilenceMinutesTrigger)
    call_only: CallOnlyTrigger = field(default_factory=CallOnlyTrigger)


@dataclass(frozen=True, slots=True)
class WakeupConfig:
    triggers: WakeupTriggers = field(default_factory=WakeupTriggers)
    allow_self_open: bool = False
    refresh_every_hours: int = 24
    soft_bounds: WakeupSoftBounds | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> WakeupConfig:
        if not raw:
            return cls()
        triggers_raw = _tolerant_dict(raw.get("triggers"))
        enm = _tolerant_dict(triggers_raw.get("every_n_messages"))
        sm = _tolerant_dict(triggers_raw.get("silence_minutes"))
        co = _tolerant_dict(triggers_raw.get("call_only"))
        soft_raw = raw.get("soft_bounds")
        soft_bounds = (
            WakeupSoftBounds(
                n_min=_tolerant_soft_bound(soft_raw.get("n_min"), minimum=N_MIN, maximum=N_MAX),
                n_max=_tolerant_soft_bound(soft_raw.get("n_max"), minimum=N_MIN, maximum=N_MAX),
                t_minutes_min=_tolerant_soft_bound(
                    soft_raw.get("t_minutes_min"), minimum=T_MINUTES_MIN, maximum=T_MINUTES_MAX
                ),
                t_minutes_max=_tolerant_soft_bound(
                    soft_raw.get("t_minutes_max"), minimum=T_MINUTES_MIN, maximum=T_MINUTES_MAX
                ),
            )
            if isinstance(soft_raw, dict)
            else None
        )
        return cls(
            triggers=WakeupTriggers(
                every_n_messages=EveryNMessagesTrigger(
                    enabled=_tolerant_bool(enm.get("enabled", False)),
                    n=clamp(_tolerant_int(enm.get("n", 3)), N_MIN, N_MAX),
                ),
                silence_minutes=SilenceMinutesTrigger(
                    enabled=_tolerant_bool(sm.get("enabled", False)),
                    t_minutes=clamp(
                        _tolerant_int(sm.get("t_minutes", 2)),
                        T_MINUTES_MIN,
                        T_MINUTES_MAX,
                    ),
                    autostop_rounds=_default_below_one(
                        _tolerant_int(sm.get("autostop_rounds", 5)),
                        default=5,
                        maximum=AUTOSTOP_HARD_CAP,
                    ),
                    autostop_max_default=_default_below_one(
                        _tolerant_int(sm.get("autostop_max_default", 100)),
                        default=100,
                        maximum=AUTOSTOP_HARD_CAP,
                    ),
                    observer_autostop_rounds=_default_below_one(
                        _tolerant_int(sm.get("observer_autostop_rounds", 50)),
                        default=50,
                        maximum=AUTOSTOP_HARD_CAP,
                    ),
                ),
                call_only=CallOnlyTrigger(
                    enabled=_tolerant_bool(co.get("enabled", False)),
                ),
            ),
            allow_self_open=_tolerant_bool(raw.get("allow_self_open", False)),
            refresh_every_hours=_default_below_one(
                _tolerant_int(raw.get("refresh_every_hours", 24)),
                default=24,
            ),
            soft_bounds=soft_bounds,
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "triggers": {
                "every_n_messages": {
                    "enabled": self.triggers.every_n_messages.enabled,
                    "n": self.triggers.every_n_messages.n,
                },
                "silence_minutes": {
                    "enabled": self.triggers.silence_minutes.enabled,
                    "t_minutes": self.triggers.silence_minutes.t_minutes,
                    "autostop_rounds": self.triggers.silence_minutes.autostop_rounds,
                    "autostop_max_default": self.triggers.silence_minutes.autostop_max_default,
                    "observer_autostop_rounds": self.triggers.silence_minutes.observer_autostop_rounds,
                },
                "call_only": {
                    "enabled": self.triggers.call_only.enabled,
                },
            },
            "allow_self_open": self.allow_self_open,
            "refresh_every_hours": self.refresh_every_hours,
        }
        if self.soft_bounds is not None:
            value["soft_bounds"] = {
                key: bound
                for key, bound in (
                    ("n_min", self.soft_bounds.n_min),
                    ("n_max", self.soft_bounds.n_max),
                    ("t_minutes_min", self.soft_bounds.t_minutes_min),
                    ("t_minutes_max", self.soft_bounds.t_minutes_max),
                )
                if bound is not None
            }
        return value

    def is_inert(self) -> bool:
        return (
            not self.triggers.every_n_messages.enabled
            and not self.triggers.silence_minutes.enabled
            and not self.triggers.call_only.enabled
        )


# ---------------------------------------------------------------------------
# Wake-up self-modification bounds (R15.07)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WakeupSoftBounds:
    """Designer-set per-agent bounds (R15.08). If absent, hard bounds apply."""

    n_min: int | None = None
    n_max: int | None = None
    t_minutes_min: int | None = None
    t_minutes_max: int | None = None


# ---------------------------------------------------------------------------
# DLQ entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DlqEntry:
    stream_id: str
    envelope: A2AEnvelope
    attempt_count: int
    last_error: str
    moved_at: datetime


# ---------------------------------------------------------------------------
# Approval gates (G.6 — R15.10–R15.14)
# ---------------------------------------------------------------------------


class ApprovalMode(str, enum.Enum):
    SINGLE = "single"
    MAJORITY = "majority"
    CONSENSUS = "consensus"


class ApprovalState(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT_LEADER = "timeout_leader"


@dataclass(frozen=True, slots=True)
class Approval:
    id: uuid.UUID
    workflow_run_id: uuid.UUID
    mode: ApprovalMode
    leader_agent_id: uuid.UUID
    approver_agent_ids: tuple[uuid.UUID, ...]
    timeout_seconds: int
    state: ApprovalState
    started_at: datetime
    ended_at: datetime | None
    # F-13: the room this gate was raised in, persisted so a chatroom-scoped
    # client can discover it after missing the approval.requested WS frame.
    # None for rows created before this column existed, and whenever the
    # gate's chatroom_id could not be resolved at creation.
    chatroom_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ApprovalVote:
    approval_id: uuid.UUID
    voter_agent_id: uuid.UUID
    vote: bool
    rationale: str | None
    cast_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalGateConfig:
    """Node-level config for an approval gate (R15.10)."""

    mode: ApprovalMode
    approvers: tuple[uuid.UUID, ...]
    leader_agent_id: uuid.UUID
    timeout_seconds: int = 300
    # What the approvers are deciding on — interpolated by the workflow node
    # and threaded into the approvers' pending-notify payloads so a voter
    # actually knows the subject of the vote.
    question: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1 or self.timeout_seconds > 86400:
            raise ValueError("timeout_seconds must be 1..86400")
        if self.leader_agent_id not in self.approvers:
            raise ValueError("leader_agent_id must be in approvers list")


# ---------------------------------------------------------------------------
# Instruct chains (G.7 — R15.15–R15.17)
# ---------------------------------------------------------------------------


class InstructionState(str, enum.Enum):
    ISSUED = "issued"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    REJECTED_LOOP = "rejected_loop"
    TIMEOUT = "timeout"


#: Terminal — once written, never overwritten (F-15). REJECTED_LOOP is only
#: ever set at INSERT, never the target of a transition.
INSTRUCTION_TERMINAL_STATES: frozenset[InstructionState] = frozenset(
    {InstructionState.COMPLETED, InstructionState.TIMEOUT, InstructionState.REJECTED_LOOP}
)

#: Allowed predecessors per target state — the instruct state machine (G.7),
#: mirrored in docs/implement/G-orchestration.md. Co-located with the enum
#: so a new member can't silently desync the CAS predicate from the states
#: it's meant to guard.
INSTRUCTION_ALLOWED_PREDECESSORS: dict[InstructionState, frozenset[InstructionState]] = {
    InstructionState.DELIVERED: frozenset({InstructionState.ISSUED}),
    InstructionState.COMPLETED: frozenset({InstructionState.ISSUED, InstructionState.DELIVERED}),
    InstructionState.TIMEOUT: frozenset({InstructionState.ISSUED, InstructionState.DELIVERED}),
}


@dataclass(frozen=True, slots=True)
class Instruction:
    id: uuid.UUID
    chain_id: uuid.UUID
    path: tuple[uuid.UUID, ...]
    depth: int
    issuer_agent_id: uuid.UUID
    target_agent_id: uuid.UUID
    payload: dict[str, Any]
    state: InstructionState
    issued_at: datetime
    resolved_at: datetime | None


INSTRUCT_MAX_CHAIN_DEPTH: int = 5
INSTRUCT_MAX_CHAIN_DEPTH_HARD: int = 20
INSTRUCT_MAX_PER_WAKEUP: int = 5
INSTRUCT_MAX_CHAIN_SECONDS: int = 120


# ---------------------------------------------------------------------------
# Agent instances / sub-agents (G.8 — R15.18–R15.23)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentInstance:
    id: uuid.UUID
    agent_id: uuid.UUID
    parent_id: uuid.UUID | None
    chatroom_id: uuid.UUID | None
    run_context: dict[str, Any]
    task_description: str | None
    state: str  # running | completed | error
    spawned_at: datetime
    destroyed_at: datetime | None

    @property
    def is_subagent(self) -> bool:
        return self.parent_id is not None


SUBAGENT_MAX_CONCURRENT_DEFAULT: int = 3
SUBAGENT_MAX_CONCURRENT_HARD: int = 20
SUBAGENT_RETENTION_DAYS: int = 30

SUBAGENT_INHERITANCE: dict[str, bool] = {
    "key_group_id": True,
    "system_prompt": True,
    "model_hint": True,
    "a2a_enabled": False,
    "mcp_servers": True,
    "skills": True,
    "rag_config_id": False,
    "context_mode": True,
    "context_token_cap": True,
    "wakeup_config": False,
    "can_create_subagent": False,
    "can_instruct": False,
    "can_approve": False,
}


__all__ = [
    "A2A_BROADCAST_MAX_RECIPIENTS",
    "A2A_CALL_MAX_DEPTH",
    "A2AEnvelope",
    "A2AMessageType",
    "AUTOSTOP_HARD_CAP",
    "AgentInstance",
    "Approval",
    "ApprovalGateConfig",
    "ApprovalMode",
    "ApprovalState",
    "ApprovalVote",
    "CallOnlyTrigger",
    "DlqEntry",
    "EveryNMessagesTrigger",
    "INSTRUCT_MAX_CHAIN_DEPTH",
    "INSTRUCT_MAX_CHAIN_DEPTH_HARD",
    "INSTRUCT_MAX_CHAIN_SECONDS",
    "INSTRUCT_MAX_PER_WAKEUP",
    "Instruction",
    "InstructionState",
    "N_MAX",
    "N_MIN",
    "SUBAGENT_INHERITANCE",
    "SUBAGENT_MAX_CONCURRENT_DEFAULT",
    "SUBAGENT_MAX_CONCURRENT_HARD",
    "SUBAGENT_RETENTION_DAYS",
    "SilenceMinutesTrigger",
    "T_MINUTES_MAX",
    "T_MINUTES_MIN",
    "WakeupConfig",
    "WakeupSoftBounds",
    "WakeupTriggers",
    "clamp",
]
