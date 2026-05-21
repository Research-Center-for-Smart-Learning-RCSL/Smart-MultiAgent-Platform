"""Orchestration domain errors → RFC 7807 registration.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3).

API-5: `WakeupClampApplied` and `ApprovalTimeoutLeader` are intentionally NOT
mapped here. They are *advisory outcomes* (a wake-up value was clamped; a
consensus vote fell back to the leader's verdict), not request failures — the
domain comments call them out as "not an error per se". Mapping them to HTTP
200 made any request that raised one return `200` with an RFC-7807 *problem*
body, which breaks clients that branch on status. Advisory outcomes must be
returned as values on the success path, never raised past a handler. Nothing
in the codebase raises them today; if one is ever raised it now hits the 500
fallback — a loud signal to fix the call site rather than a silent fake-200.
"""

from __future__ import annotations

from fastapi import FastAPI

from contexts.orchestration.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.A2ATimeout: (
        "a2a-timeout",
        504,
        "A2A sync call timed out",
    ),
    errors.A2AForbidden: (
        "a2a-forbidden",
        403,
        "A2A call forbidden by scope rules",
    ),
    errors.A2ADeliveryFailed: (
        "a2a-delivery-failed",
        502,
        "A2A message delivery exhausted retries",
    ),
    errors.WakeupFieldReadonly: (
        "orchestration/wakeup-field-readonly",
        422,
        "Only every_n_messages and silence_minutes are self-modifiable",
    ),
    errors.InstructLoopDetected: (
        "instruct-loop-detected",
        409,
        "Instruct chain contains a cycle",
    ),
    errors.InstructBudgetExceeded: (
        "instruct-budget-exceeded",
        429,
        "Instruct depth/count/time cap exceeded",
    ),
    errors.SubagentDepthExceeded: (
        "subagent-depth-exceeded",
        409,
        "Sub-agent recursion depth exceeded (max 1)",
    ),
    errors.SubagentConcurrencyExceeded: (
        "subagent-concurrency-exceeded",
        429,
        "Max concurrent sub-agents exceeded",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.OrchestrationError, _MAP)


__all__ = ["register"]
