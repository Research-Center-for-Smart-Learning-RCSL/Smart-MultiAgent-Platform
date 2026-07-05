"""prompt_studio domain errors → RFC 7807 registration (§29).

Dispatch + fallback live in ``shared_kernel.errors.context_handler``.
"""

from __future__ import annotations

from fastapi import FastAPI

from contexts.prompt_studio.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.AssistantConfigNotFound: (
        "prompt-studio/config-not-found",
        404,
        "Assistant config not found",
    ),
    errors.TemplateNotFound: ("prompt-studio/template-not-found", 404, "Prompt template not found"),
    errors.VersionMismatch: ("prompt-studio/version-mismatch", 412, "Version mismatch (If-Match)"),
    errors.PinnedKeyNotOwned: (
        "prompt-studio/key-not-owned",
        403,
        "Pinned key must be owned by the configurer",
    ),
    errors.PinnedKeyCapabilityMismatch: (
        "prompt-studio/key-capability",
        422,
        "Pinned key's provider does not support chat completions",
    ),
    errors.FileTooLarge: ("prompt-studio/file-too-large", 413, "Reference file exceeds the size limit"),
    errors.FileFormatUnsupported: (
        "prompt-studio/file-format",
        415,
        "Unsupported reference-file format",
    ),
    errors.ExtractedTextBudgetExceeded: (
        "prompt-studio/text-budget",
        413,
        "Reference-file extracted-text budget exceeded",
    ),
    errors.FileInfected: ("prompt-studio/file-infected", 422, "Reference file failed virus scan"),
    errors.ReferenceFileNotFound: ("prompt-studio/file-not-found", 404, "Reference file not found"),
    errors.ReferenceFileExtractionFailed: (
        "prompt-studio/file-extraction-failed",
        422,
        "Reference file text could not be extracted",
    ),
    errors.TemplateLimitReached: (
        "prompt-studio/template-limit",
        409,
        "Per-scope prompt-template limit reached",
    ),
    errors.AssistantUnavailable: (
        "prompt-studio/unavailable",
        404,
        "No enabled assistant config resolves for this project",
    ),
    errors.DailyQuotaExceeded: (
        "prompt-studio/quota-exceeded",
        429,
        "Per-user daily assistant request cap exceeded",
    ),
    errors.SessionNotFound: ("prompt-studio/session-not-found", 404, "Assistant session not found"),
    errors.SessionLimitReached: (
        "prompt-studio/session-limit",
        429,
        "Assistant session message cap reached",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.PromptStudioError, _MAP)


__all__ = ["register"]
