"""Workflow domain errors → RFC 7807 registration."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.workflow.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

_MAP: dict[type[errors.WorkflowError], tuple[str, int, str]] = {
    errors.WorkflowNotFound: (
        "workflow-not-found",
        404,
        "Workflow not found",
    ),
    errors.WorkflowVersionConflict: (
        "workflow-version-conflict",
        412,
        "Optimistic lock conflict",
    ),
    errors.WorkflowValidationFailed: (
        "workflow-validation",
        422,
        "Workflow definition validation failed",
    ),
    errors.WorkflowRunNotFound: (
        "workflow-run-not-found",
        404,
        "Workflow run not found",
    ),
    errors.WorkflowRunNotCancellable: (
        "workflow-run-not-cancellable",
        409,
        "Run cannot be cancelled in its current state",
    ),
    errors.WorkflowDeleted: (
        "workflow-deleted",
        410,
        "Workflow has been deleted",
    ),
    errors.SELBudgetExceeded: (
        "sel-budget-exceeded",
        422,
        "Expression evaluation budget exceeded",
    ),
    errors.SELSyntaxError: (
        "sel-syntax-error",
        422,
        "Expression syntax error",
    ),
    errors.SELForbiddenConstruct: (
        "sel-forbidden-construct",
        422,
        "Forbidden expression construct",
    ),
    errors.WorkflowStepFailed: (
        "workflow-step-failed",
        500,
        "Workflow step execution failed",
    ),
    errors.WorkflowRunTimeout: (
        "workflow-run-timeout",
        504,
        "Workflow run timed out",
    ),
    errors.WorkflowRunCancelled: (
        "workflow-run-cancelled",
        409,
        "Workflow run was cancelled",
    ),
    errors.LoopGuardExceeded: (
        "loop-guard-exceeded",
        422,
        "Loop guard limit exceeded",
    ),
}


async def _handler(request: Request, exc: errors.WorkflowError) -> JSONResponse:
    slug, status_code, title = _MAP.get(
        type(exc),
        ("workflow/generic", 400, "Workflow error"),
    )
    extras: dict[str, Any] = {}
    if isinstance(exc, errors.WorkflowValidationFailed):
        extras["errors"] = exc.lint_errors
        extras["warnings"] = exc.lint_warnings

    problem = Problem(
        type=problem_type(slug),
        title=title,
        status=status_code,
        detail=str(exc),
        extras=extras,
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status_code, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.WorkflowError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
