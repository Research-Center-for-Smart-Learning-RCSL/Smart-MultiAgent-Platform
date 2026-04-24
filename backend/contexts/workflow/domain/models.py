"""Workflow domain dataclasses — framework-free.

Covers: Workflow, WorkflowRun, WorkflowStep, plus enums for node types,
trigger types, run states, and step states. These mirror the spec in
`docs/workflow.schema.json` and `docs/workflow.schema.md`.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeType(str, enum.Enum):
    TRIGGER = "trigger"
    AGENT_INVOCATION = "agent_invocation"
    APPROVAL_GATE = "approval_gate"
    CONDITION = "condition"
    INSTRUCT = "instruct"
    SUBAGENT_SPAWN = "subagent_spawn"
    WAIT_FOR_EVENT = "wait_for_event"
    PARALLEL = "parallel"
    JOIN = "join"
    SET_VARIABLE = "set_variable"
    END = "end"


class TriggerType(str, enum.Enum):
    MANUAL = "manual"
    CRON = "cron"
    MESSAGE_RECEIVED = "message_received"
    A2A_EVENT = "a2a_event"
    WAKEUP_SIGNAL = "wakeup_signal"


class RunState(str, enum.Enum):
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class OnErrorStrategy(str, enum.Enum):
    FAIL = "fail"
    CONTINUE = "continue"
    RETRY = "retry"
    FALLBACK = "fallback"


class JoinMode(str, enum.Enum):
    ALL = "all"
    ANY = "any"
    COUNT = "count"


class WaitEventType(str, enum.Enum):
    MESSAGE_IN_ROOM = "message_in_room"
    A2A_MESSAGE = "a2a_message"
    TIMER = "timer"
    VARIABLE_MATCHES = "variable_matches"


# ---------------------------------------------------------------------------
# Domain entities
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Workflow:
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    definition: dict[str, Any]
    version: int
    created_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    id: uuid.UUID
    workflow_id: uuid.UUID
    trigger_type: str
    started_by_user_id: uuid.UUID | None
    state: RunState
    variables: dict[str, Any]
    context: dict[str, Any]
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    id: uuid.UUID
    run_id: uuid.UUID
    node_id: str
    state: StepState
    started_at: datetime
    ended_at: datetime | None
    input: dict[str, Any]
    output: dict[str, Any]
    error: str | None


# ---------------------------------------------------------------------------
# Value objects for the executor protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OnErrorConfig:
    strategy: OnErrorStrategy = OnErrorStrategy.FAIL
    retry_max: int = 0
    retry_backoff_ms: int = 500
    fallback_node_id: str | None = None


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """Parsed view of one node from the definition JSONB."""
    id: str
    type: NodeType
    config: dict[str, Any]
    label: str | None = None
    on_error: OnErrorConfig = field(default_factory=OnErrorConfig)


@dataclass(frozen=True, slots=True)
class EdgeSpec:
    """Parsed view of one edge from the definition JSONB."""
    id: str
    from_node: str
    to_node: str
    from_port: str = "default"
    guard: str | None = None


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Return value of every node executor."""
    state: StepState
    output: dict[str, Any] = field(default_factory=dict)
    port: str = "default"
    error: str | None = None
    park: bool = False
    # W2/W4: step succeeded but engine must NOT follow outgoing edges (e.g. join fan-in
    # arrival that is not the final one, or fallback path already followed internally).
    skip_edges: bool = False
    # W6/W10: when park=True and timeout_ms > 0, engine enqueues a delayed timeout task.
    timeout_ms: int = 0
    timeout_task: str = ""


@dataclass(slots=True)
class RunContext:
    """Mutable context threaded through a single run execution."""
    run_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_def: dict[str, Any]
    variables: dict[str, Any]
    trigger_payload: dict[str, Any] = field(default_factory=dict)
    node_visit_counts: dict[str, int] = field(default_factory=dict)
    active_branches: int = 1
    cancelled: bool = False

    @property
    def run_max_seconds(self) -> int:
        return self.workflow_def.get("timeouts", {}).get("run_max_seconds", 3600)

    @property
    def idle_max_seconds(self) -> int:
        return self.workflow_def.get("timeouts", {}).get("idle_max_seconds", 1800)

    @property
    def max_visits_per_node(self) -> int:
        # W9: clamp to 1-1000; prevents runaway loops and unreasonably tight guards.
        raw = self.workflow_def.get("loop_guard", {}).get("max_visits_per_node", 200)
        return max(1, min(int(raw), 1000))


# ---------------------------------------------------------------------------
# Lint result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LintIssue:
    rule: int
    level: str  # "error" | "warning"
    message: str
    node_id: str | None = None
    edge_id: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    errors: list[LintIssue]
    warnings: list[LintIssue]


__all__ = [
    "EdgeSpec",
    "JoinMode",
    "LintIssue",
    "NodeSpec",
    "NodeType",
    "OnErrorConfig",
    "OnErrorStrategy",
    "RunContext",
    "RunState",
    "StepOutcome",
    "StepState",
    "TriggerType",
    "ValidationResult",
    "WaitEventType",
    "Workflow",
    "WorkflowRun",
    "WorkflowStep",
]
