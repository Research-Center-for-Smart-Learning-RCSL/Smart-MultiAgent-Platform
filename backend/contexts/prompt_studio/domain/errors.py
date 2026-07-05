"""Domain errors for the prompt_studio context (§29).

One base class carrying a stable ``code``; each concrete failure overrides it.
Mapped to RFC-7807 responses by ``interfaces/error_mapping.py``.
"""

from __future__ import annotations


class PromptStudioError(Exception):
    code: str = "prompt-studio/generic"


class AssistantConfigNotFound(PromptStudioError):
    code = "prompt-studio/config-not-found"


class TemplateNotFound(PromptStudioError):
    code = "prompt-studio/template-not-found"


class VersionMismatch(PromptStudioError):
    code = "prompt-studio/version-mismatch"


class PinnedKeyNotOwned(PromptStudioError):
    """The pinned key is not owned by the configurer of this scope."""

    code = "prompt-studio/key-not-owned"


class PinnedKeyCapabilityMismatch(PromptStudioError):
    """The pinned key's provider does not support chat completions."""

    code = "prompt-studio/key-capability"


class FileTooLarge(PromptStudioError):
    code = "prompt-studio/file-too-large"


class FileFormatUnsupported(PromptStudioError):
    code = "prompt-studio/file-format"


class ExtractedTextBudgetExceeded(PromptStudioError):
    code = "prompt-studio/text-budget"


class FileInfected(PromptStudioError):
    code = "prompt-studio/file-infected"


class ReferenceFileNotFound(PromptStudioError):
    code = "prompt-studio/file-not-found"


class ReferenceFileExtractionFailed(PromptStudioError):
    code = "prompt-studio/file-extraction-failed"


class TemplateLimitReached(PromptStudioError):
    code = "prompt-studio/template-limit"


class AssistantUnavailable(PromptStudioError):
    """No enabled config resolves for the requesting user's project."""

    code = "prompt-studio/unavailable"


class DailyQuotaExceeded(PromptStudioError):
    code = "prompt-studio/quota-exceeded"


class SessionNotFound(PromptStudioError):
    code = "prompt-studio/session-not-found"


class SessionLimitReached(PromptStudioError):
    code = "prompt-studio/session-limit"


__all__ = [
    "AssistantConfigNotFound",
    "AssistantUnavailable",
    "DailyQuotaExceeded",
    "ExtractedTextBudgetExceeded",
    "FileFormatUnsupported",
    "FileInfected",
    "FileTooLarge",
    "PinnedKeyCapabilityMismatch",
    "PinnedKeyNotOwned",
    "PromptStudioError",
    "ReferenceFileExtractionFailed",
    "ReferenceFileNotFound",
    "SessionLimitReached",
    "SessionNotFound",
    "TemplateLimitReached",
    "TemplateNotFound",
    "VersionMismatch",
]
