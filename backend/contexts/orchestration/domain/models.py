"""Orchestration domain dataclasses — framework-free.

Covers the A2A envelope (R9.13), wake-up config shape (§15.1),
and instruct/sub-agent value objects used by G.1–G.8.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
        )


# ---------------------------------------------------------------------------
# Wake-up config shape (§15.1)
# ---------------------------------------------------------------------------

# Hard caps applied at parse time so they're enforced regardless of how the
# JSONB was written (designer UI, direct DB edit, migration, etc.).
AUTOSTOP_HARD_CAP: int = 100


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


@dataclass(frozen=True, slots=True)
class CallOnlyTrigger:
    enabled: bool = False


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

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> WakeupConfig:
        if not raw:
            return cls()
        triggers_raw = raw.get("triggers") or {}
        enm = triggers_raw.get("every_n_messages") or {}
        sm = triggers_raw.get("silence_minutes") or {}
        co = triggers_raw.get("call_only") or {}
        return cls(
            triggers=WakeupTriggers(
                every_n_messages=EveryNMessagesTrigger(
                    enabled=bool(enm.get("enabled", False)),
                    n=int(enm.get("n", 3)),
                ),
                silence_minutes=SilenceMinutesTrigger(
                    enabled=bool(sm.get("enabled", False)),
                    t_minutes=int(sm.get("t_minutes", 2)),
                    autostop_rounds=min(int(sm.get("autostop_rounds", 5)), AUTOSTOP_HARD_CAP),
                    autostop_max_default=int(sm.get("autostop_max_default", 100)),
                ),
                call_only=CallOnlyTrigger(
                    enabled=bool(co.get("enabled", False)),
                ),
            ),
            allow_self_open=bool(raw.get("allow_self_open", False)),
            refresh_every_hours=int(raw.get("refresh_every_hours", 24)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
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
                },
                "call_only": {
                    "enabled": self.triggers.call_only.enabled,
                },
            },
            "allow_self_open": self.allow_self_open,
            "refresh_every_hours": self.refresh_every_hours,
        }

    def is_inert(self) -> bool:
        return (
            not self.triggers.every_n_messages.enabled
            and not self.triggers.silence_minutes.enabled
            and not self.triggers.call_only.enabled
        )


# ---------------------------------------------------------------------------
# Wake-up self-modification bounds (R15.07)
# ---------------------------------------------------------------------------

N_MIN: int = 1
N_MAX: int = 1000
T_MINUTES_MIN: int = 1
T_MINUTES_MAX: int = 1440


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
    "prompt_strategy": True,
    "model_hint": True,
    "a2a_enabled": False,
    "mcp_servers": True,
    "rag_config_id": False,
    "graphrag_config_id": False,
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
]
