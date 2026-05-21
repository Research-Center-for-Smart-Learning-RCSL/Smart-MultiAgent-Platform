"""Workflow domain errors → RFC 7807 registration.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from contexts.workflow.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
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


def _extras(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, errors.WorkflowValidationFailed):
        return {"errors": exc.lint_errors, "warnings": exc.lint_warnings}
    return {}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.WorkflowError, _MAP, _extras)


__all__ = ["register"]
