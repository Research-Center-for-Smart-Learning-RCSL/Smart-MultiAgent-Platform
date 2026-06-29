"""Orchestration domain errors → RFC 7807 slugs."""

from __future__ import annotations


class OrchestrationError(Exception):
    code: str = "orchestration/generic"


class A2ATimeout(OrchestrationError):
    """Sync call did not receive a reply within the deadline (R9.15)."""

    code = "a2a-timeout"


class A2AForbidden(OrchestrationError):
    """Re-raised from agents.domain when the scope check fails at send()."""

    code = "a2a-forbidden"


class A2ADeliveryFailed(OrchestrationError):
    """Message exhausted retries and was moved to DLQ."""

    code = "a2a-delivery-failed"


class A2ACallLoop(OrchestrationError):
    """Synchronous A2A call would re-enter an agent already on the call stack.

    A CALL runs the callee's turn inline in its consumer loop, so A->B->A would
    stall both loops to timeout; reject the cycle instead (R9.15)."""

    code = "a2a-call-loop"


class A2ACallDepthExceeded(OrchestrationError):
    """Synchronous A2A call nesting exceeded the hard depth cap (R9.15)."""

    code = "a2a-call-depth-exceeded"


class WakeupClampApplied(OrchestrationError):
    """Self-modification value was clamped to hard/soft bounds (R15.07).

    Not an error per se — callers may want to know a clamp happened.
    Audit is the primary sink; this exception is raised only when
    strict mode is desired.
    """

    code = "orchestration/wakeup-clamped"


class WakeupFieldReadonly(OrchestrationError):
    """Agent tried to modify a field other than n / t_minutes (R15.06)."""

    code = "orchestration/wakeup-field-readonly"


class InstructLoopDetected(OrchestrationError):
    """Instruct chain contains a cycle (R15.16 rule 1)."""

    code = "instruct-loop-detected"


class InstructBudgetExceeded(OrchestrationError):
    """Depth, count, or wall-clock cap hit (R15.16 rules 2–4)."""

    code = "instruct-budget-exceeded"


class SubagentDepthExceeded(OrchestrationError):
    """Sub-agent tried to spawn another sub-agent (R15.19)."""

    code = "subagent-depth-exceeded"


class SubagentConcurrencyExceeded(OrchestrationError):
    """Parent exceeded max_subagents_alive_simultaneously (R15.20)."""

    code = "subagent-concurrency-exceeded"


class ApprovalTimeoutLeader(OrchestrationError):
    """Consensus approval timed out; leader's verdict used (R15.13)."""

    code = "approval-timeout-leader"


__all__ = [
    "A2ACallDepthExceeded",
    "A2ACallLoop",
    "A2ADeliveryFailed",
    "A2AForbidden",
    "A2ATimeout",
    "ApprovalTimeoutLeader",
    "InstructBudgetExceeded",
    "InstructLoopDetected",
    "OrchestrationError",
    "SubagentConcurrencyExceeded",
    "SubagentDepthExceeded",
    "WakeupClampApplied",
    "WakeupFieldReadonly",
]
