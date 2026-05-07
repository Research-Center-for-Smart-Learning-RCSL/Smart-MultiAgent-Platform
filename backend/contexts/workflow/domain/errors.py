"""Workflow domain errors → RFC 7807 slugs."""

from __future__ import annotations

from typing import Any


class WorkflowError(Exception):
    code: str = "workflow/generic"


class WorkflowNotFound(WorkflowError):
    code = "workflow-not-found"


class WorkflowVersionConflict(WorkflowError):
    """If-Match optimistic lock mismatch (R14.06 / R9.02)."""

    code = "workflow-version-conflict"


class WorkflowValidationFailed(WorkflowError):
    """JSON Schema or semantic linter rejected the definition."""

    code = "workflow-validation"

    def __init__(self, errors: list[dict[str, Any]], warnings: list[dict[str, Any]] | None = None) -> None:
        self.lint_errors = errors
        self.lint_warnings = warnings or []
        super().__init__(f"{len(errors)} validation error(s)")


class WorkflowRunNotFound(WorkflowError):
    code = "workflow-run-not-found"


class WorkflowRunNotCancellable(WorkflowError):
    code = "workflow-run-not-cancellable"


class WorkflowDeleted(WorkflowError):
    code = "workflow-deleted"


class SELBudgetExceeded(WorkflowError):
    """Expression evaluation exceeded CPU / depth / length budget."""

    code = "sel-budget-exceeded"


class SELSyntaxError(WorkflowError):
    code = "sel-syntax-error"


class SELForbiddenConstruct(WorkflowError):
    code = "sel-forbidden-construct"


class WorkflowStepFailed(WorkflowError):
    code = "workflow-step-failed"


class WorkflowRunTimeout(WorkflowError):
    code = "workflow-run-timeout"


class WorkflowRunCancelled(WorkflowError):
    code = "workflow-run-cancelled"


class LoopGuardExceeded(WorkflowError):
    code = "loop-guard-exceeded"


__all__ = [
    "LoopGuardExceeded",
    "SELBudgetExceeded",
    "SELForbiddenConstruct",
    "SELSyntaxError",
    "WorkflowDeleted",
    "WorkflowError",
    "WorkflowNotFound",
    "WorkflowRunCancelled",
    "WorkflowRunNotCancellable",
    "WorkflowRunNotFound",
    "WorkflowRunTimeout",
    "WorkflowStepFailed",
    "WorkflowValidationFailed",
    "WorkflowVersionConflict",
]
