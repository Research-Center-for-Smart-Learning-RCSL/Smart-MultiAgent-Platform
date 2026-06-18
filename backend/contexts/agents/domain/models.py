"""Agents domain dataclasses — framework-free."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class AgentModelHint(str, enum.Enum):
    CLAUDE = "claude"
    OPENAI = "openai"
    GEMINI = "gemini"


class PromptStrategy(str, enum.Enum):
    FULL = "full"
    LAZY = "lazy"


class ContextMode(str, enum.Enum):
    GENERAL = "general"
    COMPACT = "compact"


class McpSource(str, enum.Enum):
    BUILTIN = "builtin"
    URL = "url"
    PACKAGE = "package"


@dataclass(frozen=True, slots=True)
class Agent:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    model_hint: AgentModelHint
    model_id: str | None
    key_group_id: uuid.UUID
    system_prompt: str
    prompt_strategy: PromptStrategy
    rag_config_id: uuid.UUID | None
    graphrag_config_id: uuid.UUID | None
    context_mode: ContextMode
    context_token_cap: int | None
    a2a_enabled: bool
    wakeup_config: dict[str, Any]
    wakeup_authored_snapshot: dict[str, Any] | None
    workflow_capabilities: dict[str, Any]
    version: int
    deleted_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class McpBinding:
    id: uuid.UUID
    agent_id: uuid.UUID
    source: McpSource
    reference: str
    allowed_tools: tuple[str, ...]
    config: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentDraft:
    """Payload shape for create + patch. `None` means "unchanged" on patch."""

    name: str | None = None
    model_hint: AgentModelHint | None = None
    model_id: str | None = None
    key_group_id: uuid.UUID | None = None
    system_prompt: str | None = None
    prompt_strategy: PromptStrategy | None = None
    rag_config_id: uuid.UUID | None = None
    graphrag_config_id: uuid.UUID | None = None
    context_mode: ContextMode | None = None
    context_token_cap: int | None = None
    a2a_enabled: bool | None = None
    wakeup_config: dict[str, Any] | None = None
    workflow_capabilities: dict[str, Any] | None = None
    # Sentinel fields explicitly clearing a nullable column. The patch API
    # cannot tell "omitted" from "null" via `None` alone, so the service
    # drives this via explicit booleans set by the router.
    clear_model_id: bool = False
    clear_rag_config: bool = False
    clear_graphrag_config: bool = False
    clear_context_token_cap: bool = False


__all__ = [
    "Agent",
    "AgentDraft",
    "AgentModelHint",
    "ContextMode",
    "McpBinding",
    "McpSource",
    "PromptStrategy",
]
