"""Domain types for MCP bindings, egress allowlist, and built-in tool results.

Framework-free value objects — the application, infrastructure, and interface
layers all import from this module. Value objects are frozen + slotted; the
tuple-backed ``allowed_tools`` mirrors the domain model in ``models.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contexts.agents.domain.models import McpSource


@dataclass(frozen=True, slots=True)
class McpServerDraft:
    """Input payload for ``POST /api/agents/{id}/mcp``.

    ``auth`` is the plaintext auth material (bearer token, header map, etc.)
    that the service envelope-encrypts before persisting into the ``config``
    JSONB column. Once sealed it never leaves the server.
    """

    source: McpSource
    reference: str
    allowed_tools: tuple[str, ...] = ()
    config: dict[str, Any] = field(default_factory=dict)
    auth: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EgressAllowlistEntry:
    id: uuid.UUID
    project_id: uuid.UUID
    hostname: str
    added_by_user_id: uuid.UUID | None
    added_at: datetime
    note: str | None


@dataclass(frozen=True, slots=True)
class McpTestResult:
    """Return value for ``POST /api/agents/{id}/mcp/{mcp_id}/test`` (R12.01)."""

    ok: bool
    tool_names: tuple[str, ...]
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    """Structured result of any tool invocation (file/code_exec/web_search).

    ``stdout`` / ``stderr`` are trimmed to the per-tool caps by the caller; the
    value object itself does not truncate.
    """

    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published_at: datetime | None
    score: float


@dataclass(frozen=True, slots=True)
class StagedFile:
    """A user-uploaded file to copy into the code_exec session workspace."""

    filename: str
    data: bytes


__all__ = [
    "EgressAllowlistEntry",
    "McpServerDraft",
    "McpTestResult",
    "SearchResult",
    "StagedFile",
    "ToolCallResult",
]
