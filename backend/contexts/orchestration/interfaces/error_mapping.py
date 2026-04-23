"""Orchestration domain errors → RFC 7807 registration."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.orchestration.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

_MAP: dict[type[errors.OrchestrationError], tuple[str, int, str]] = {
    errors.A2ATimeout: (
        "a2a-timeout", 504, "A2A sync call timed out",
    ),
    errors.A2AForbidden: (
        "a2a-forbidden", 403, "A2A call forbidden by scope rules",
    ),
    errors.A2ADeliveryFailed: (
        "a2a-delivery-failed", 502, "A2A message delivery exhausted retries",
    ),
    errors.WakeupClampApplied: (
        "orchestration/wakeup-clamped", 200, "Wake-up value was clamped to bounds",
    ),
    errors.WakeupFieldReadonly: (
        "orchestration/wakeup-field-readonly", 422,
        "Only every_n_messages and silence_minutes are self-modifiable",
    ),
    errors.InstructLoopDetected: (
        "instruct-loop-detected", 409, "Instruct chain contains a cycle",
    ),
    errors.InstructBudgetExceeded: (
        "instruct-budget-exceeded", 429, "Instruct depth/count/time cap exceeded",
    ),
    errors.SubagentDepthExceeded: (
        "subagent-depth-exceeded", 409, "Sub-agent recursion depth exceeded (max 1)",
    ),
    errors.SubagentConcurrencyExceeded: (
        "subagent-concurrency-exceeded", 429,
        "Max concurrent sub-agents exceeded",
    ),
    errors.ApprovalTimeoutLeader: (
        "approval-timeout-leader", 200,
        "Consensus timed out; leader verdict applied",
    ),
}


async def _handler(request: Request, exc: errors.OrchestrationError) -> JSONResponse:
    slug, status, title = _MAP.get(
        type(exc), ("orchestration/generic", 400, "Orchestration error"),
    )
    problem = Problem(
        type=problem_type(slug), title=title, status=status, detail=str(exc),
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.OrchestrationError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
