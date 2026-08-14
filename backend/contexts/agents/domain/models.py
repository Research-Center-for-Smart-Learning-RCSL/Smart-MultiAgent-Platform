"""Agents domain dataclasses — framework-free."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class AgentModelHint(str, enum.Enum):
    CLAUDE = "claude"
    OPENAI = "openai"
    GEMINI = "gemini"


# Per-provider chat-model catalog surfaced to the agent-config UI as preset
# choices. `DEFAULT_CHAT_MODELS` is the model the runtime falls back to when an
# agent leaves `model_id` unset — turn_engine imports it from here so the
# default advertised to the UI and the default the runtime actually uses can
# never drift. Each provider's default MUST appear in its catalog tuple.
#
# Curated preset lists per provider. Verified against each provider's official
# model docs in 2026-06 — re-check periodically, since provider model lineups
# move fast (BYO-key users can always pick "custom" for a model id not listed
# here, so this list only needs to cover the common choices, not every model).
CHAT_MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    "claude": ("claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"),
    "openai": ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini"),
    "gemini": ("gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash"),
}

DEFAULT_CHAT_MODELS: dict[str, str] = {
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-5.4",
    "gemini": "gemini-3.5-flash",
}

# Per-provider context-window limits exposed through the model-catalog API so
# the frontend never maintains a second copy that could drift from the runtime.
CONTEXT_LIMITS: dict[str, int] = {
    "claude": 200_000,
    "openai": 128_000,
    "gemini": 1_000_000,
}

assert set(DEFAULT_CHAT_MODELS) == set(CHAT_MODEL_CATALOG), (
    "DEFAULT_CHAT_MODELS and CHAT_MODEL_CATALOG must have identical provider keys"
)
assert all(DEFAULT_CHAT_MODELS[p] in models for p, models in CHAT_MODEL_CATALOG.items()), (
    "every DEFAULT_CHAT_MODELS value must appear in its provider's CHAT_MODEL_CATALOG tuple"
)
assert set(CONTEXT_LIMITS) == set(CHAT_MODEL_CATALOG), (
    "CONTEXT_LIMITS and CHAT_MODEL_CATALOG must have identical provider keys"
)


@dataclass(frozen=True, slots=True)
class ChatModelCatalogEntry:
    """One provider's preset chat models plus its runtime default."""

    provider: str
    models: tuple[str, ...]
    default: str
    context_limit: int


def chat_model_catalog() -> tuple[ChatModelCatalogEntry, ...]:
    return tuple(
        ChatModelCatalogEntry(
            provider=p,
            models=models,
            default=DEFAULT_CHAT_MODELS[p],
            context_limit=CONTEXT_LIMITS[p],
        )
        for p, models in CHAT_MODEL_CATALOG.items()
    )


# Upper bound on `agents.skill_index_token_cap`, mirrored by the
# `agents_skill_index_cap_bounded` CHECK in migration 0056. The API validates against
# this so an over-cap value is a 422 rather than an IntegrityError 500.
MAX_SKILL_INDEX_TOKEN_CAP = 16_000

# Upper bound on `agents.context_token_cap`, mirrored by the
# `agents_context_cap_bounded` CHECK in migration 0057. Derived from CONTEXT_LIMITS
# rather than a literal: above the widest provider window the value cannot help any
# provider, so this is the highest number that is ever meaningful and the lowest that
# rejects nothing legitimate. Same shape as MAX_SKILL_INDEX_TOKEN_CAP above.
MAX_CONTEXT_TOKEN_CAP = max(CONTEXT_LIMITS.values())


class ContextMode(str, enum.Enum):
    GENERAL = "general"
    COMPACT = "compact"


class AgentEffort(str, enum.Enum):
    """Cross-provider reasoning-effort level. The common subset all three
    providers accept; mapped per-provider at the adapter boundary
    (Claude ``output_config.effort`` / OpenAI ``reasoning_effort`` /
    Gemini ``thinkingConfig.thinkingLevel``). ``None`` on an agent means the
    parameter is not sent and the provider's own default applies."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class McpSource(str, enum.Enum):
    BUILTIN = "builtin"
    URL = "url"
    PACKAGE = "package"


class AgentToolType(str, enum.Enum):
    HOSTED_MCP = "hosted_mcp"
    HOSTED_WEB_SEARCH = "hosted_web_search"
    HOSTED_CODE_INTERPRETER = "hosted_code_interpreter"
    HOSTED_FILE_WORKSPACE = "hosted_file_workspace"
    HOSTED_FILE_SEARCH = "hosted_file_search"
    LOCAL_FUNCTION = "local_function"
    LOCAL_SHELL = "local_shell"


SINGLETON_TOOL_TYPES: frozenset[AgentToolType] = frozenset(
    {
        AgentToolType.HOSTED_WEB_SEARCH,
        AgentToolType.HOSTED_CODE_INTERPRETER,
        AgentToolType.HOSTED_FILE_WORKSPACE,
        AgentToolType.HOSTED_FILE_SEARCH,
    }
)


@dataclass(frozen=True, slots=True)
class Agent:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    model_hint: AgentModelHint
    model_id: str | None
    effort: AgentEffort | None
    key_group_id: uuid.UUID
    system_prompt: str
    rag_config_id: uuid.UUID | None
    knowmap_config_id: uuid.UUID | None
    context_mode: ContextMode
    # Compaction ceiling in R9.10 compact mode; None means the 75%-of-provider-window
    # default. Upper-bounded at the DB (<= MAX_CONTEXT_TOKEN_CAP, currently 1000000).
    context_token_cap: int | None
    # Cap on the §31 skills index block; None means the 3000 default. Upper-bounded at
    # the DB (<= MAX_SKILL_INDEX_TOKEN_CAP, currently 16000), same shape as
    # context_token_cap above.
    skill_index_token_cap: int | None
    temperature: float | None
    top_p: float | None
    seed: int | None
    a2a_enabled: bool
    wakeup_config: dict[str, Any]
    wakeup_authored_snapshot: dict[str, Any] | None
    workflow_capabilities: dict[str, Any]
    version: int
    deleted_at: datetime | None
    created_at: datetime
    wakeup_last_refreshed_at: datetime | None = None


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
class AgentTool:
    id: uuid.UUID
    agent_id: uuid.UUID
    tool_type: AgentToolType
    enabled: bool
    display_name: str | None
    config: dict[str, Any]
    created_at: datetime
    # Server-written MCP tool contract captured at probe ("Test") time --
    # never reachable through AgentToolCreateIn/AgentToolPatchIn. Empty/None
    # degrades to the permissive fallback schema, never an error.
    mcp_captured_tools: tuple[McpToolSpec, ...] = ()
    mcp_captured_at: datetime | None = None

    def is_singleton(self) -> bool:
        return self.tool_type in SINGLETON_TOOL_TYPES

    def mcp_source(self) -> str:
        """MCP source ('url' or 'package') — only valid for HOSTED_MCP rows."""
        return str(self.config.get("source", ""))

    def mcp_reference(self) -> str:
        """MCP server reference — only valid for HOSTED_MCP rows."""
        return str(self.config.get("reference", ""))

    def mcp_allowed_tools(self) -> tuple[str, ...]:
        """Whitelist of tool names exposed by an MCP server."""
        raw = self.config.get("allowed_tools")
        if isinstance(raw, (list | tuple)):
            return tuple(str(t) for t in raw)
        return ()

    def mcp_captured_tool(self, mcp_tool: str) -> McpToolSpec | None:
        """The captured contract for one upstream tool name, if captured."""
        return next((spec for spec in self.mcp_captured_tools if spec.name == mcp_tool), None)


@dataclass(frozen=True, slots=True)
class McpToolSpec:
    """One tool's contract as captured from an MCP server's ``tools/list``.

    ``input_schema`` defaults to the permissive shape so a caller that only
    has the legacy bare-name probe form (backend/image version skew) can
    still construct a spec without inventing a schema.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "additionalProperties": True}
    )


@dataclass(frozen=True, slots=True)
class ToolProbeResult:
    """Outcome of a tool reachability/test probe (hosted_mcp or local_function)."""

    ok: bool
    tool_names: tuple[str, ...] = ()
    tools: tuple[McpToolSpec, ...] = ()
    status: int | None = None
    duration_ms: int = 0
    error: str | None = None
    # Non-blocking advisories from persisting a captured MCP tool contract
    # (e.g. a schema exceeded the size/property/$ref caps and fell back to
    # permissive) -- always empty for local_function probes.
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    id: uuid.UUID
    agent_id: uuid.UUID
    path: str
    size_bytes: int
    sha256: str
    mime: str
    minio_key: str
    created_by: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AgentDraft:
    """Payload shape for create + patch. `None` means "unchanged" on patch."""

    name: str | None = None
    model_hint: AgentModelHint | None = None
    model_id: str | None = None
    effort: AgentEffort | None = None
    key_group_id: uuid.UUID | None = None
    system_prompt: str | None = None
    rag_config_id: uuid.UUID | None = None
    knowmap_config_id: uuid.UUID | None = None
    context_mode: ContextMode | None = None
    context_token_cap: int | None = None
    skill_index_token_cap: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    a2a_enabled: bool | None = None
    wakeup_config: dict[str, Any] | None = None
    wakeup_last_refreshed_at: datetime | None = None
    workflow_capabilities: dict[str, Any] | None = None
    # Sentinel fields explicitly clearing a nullable column. The patch API
    # cannot tell "omitted" from "null" via `None` alone, so the service
    # drives this via explicit booleans set by the router.
    clear_model_id: bool = False
    clear_effort: bool = False
    clear_rag_config: bool = False
    clear_knowmap_config: bool = False
    clear_context_token_cap: bool = False
    clear_skill_index_token_cap: bool = False
    clear_temperature: bool = False
    clear_top_p: bool = False
    clear_seed: bool = False
    # `wakeup_config` writes are additive by default so a partial payload cannot
    # delete designer keys (R15.08). The G.5 periodic refresh is the one writer
    # that must *replace*: it restores the authored snapshot, and merging would
    # leave live-config keys the snapshot never had — so the post-refresh config
    # would never equal the snapshot and every later sweep would refresh again.
    # Not reachable from the API: the router builds this draft field by field.
    replace_wakeup_config: bool = False


__all__ = [
    "CHAT_MODEL_CATALOG",
    "CONTEXT_LIMITS",
    "DEFAULT_CHAT_MODELS",
    "MAX_CONTEXT_TOKEN_CAP",
    "MAX_SKILL_INDEX_TOKEN_CAP",
    "SINGLETON_TOOL_TYPES",
    "Agent",
    "AgentDraft",
    "AgentEffort",
    "AgentModelHint",
    "AgentTool",
    "AgentToolType",
    "ChatModelCatalogEntry",
    "ContextMode",
    "McpBinding",
    "McpSource",
    "McpToolSpec",
    "WorkspaceFile",
    "chat_model_catalog",
]
