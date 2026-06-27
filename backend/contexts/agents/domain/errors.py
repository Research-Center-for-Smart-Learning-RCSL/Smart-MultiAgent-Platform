"""Agents domain errors → RFC 7807 slugs."""

from __future__ import annotations


class AgentsError(Exception):
    code: str = "agents/generic"


class AgentNotFound(AgentsError):
    code = "agents/not-found"


class AgentNameTaken(AgentsError):
    code = "agents/name-taken"


class AgentVersionMismatch(AgentsError):
    code = "agents/version-mismatch"


class AgentCapExceeded(AgentsError):
    """R9.01 — 1 000 agents per project."""

    code = "agents/agent-cap-exceeded"


class KeyGroupOutOfProject(AgentsError):
    """Attached Key Group does not belong to the agent's project."""

    code = "agents/key-group-out-of-project"


class KeyGroupNoMatchingProvider(AgentsError):
    """Key Group has no carried key matching the agent's model_hint provider."""

    code = "agents/key-group-no-matching-provider"


class RagConfigOutOfProject(AgentsError):
    """Attached RAG config does not belong to the agent's project (SEC-H1).

    Surfaced as 404 — an attacker probing another tenant's config UUID must
    not be able to tell "exists elsewhere" from "does not exist".
    """

    code = "agents/rag-config-not-found"


class GraphRagConfigOutOfProject(AgentsError):
    """Attached GraphRAG config does not belong to the agent's project (SEC-H1)."""

    code = "agents/graphrag-config-not-found"


class A2AForbidden(AgentsError):
    """R9.17 — scope check failed."""

    code = "agents/a2a-forbidden"


class McpBindingNotFound(AgentsError):
    code = "agents/mcp-binding-not-found"


class AgentToolNotFound(AgentsError):
    code = "agents/tool-not-found"


class AgentToolTypeImmutable(AgentsError):
    """Singleton tool types are auto-provisioned; cannot be created via add."""

    code = "agents/tool-type-immutable"


class ToolNotAvailable(AgentsError):
    """Tool type is not implemented yet (e.g. local_shell)."""

    code = "agents/tool-not-available"


class FileSearchNeedsKnowledge(AgentsError):
    """File Search requires a RAG config (knowledge source) attached to the agent."""

    code = "agents/file-search-needs-knowledge-source"


class McpTestFailed(AgentsError):
    """Sandbox probe of an MCP server did not complete cleanly."""

    code = "agents/mcp-test-failed"


class McpEgressDenied(AgentsError):
    """Egress Proxy rejected the outbound call (R12.04)."""

    code = "mcp-egress-denied"


class McpTimeout(AgentsError):
    """Sandbox probe exceeded the per-call wall-clock budget."""

    code = "mcp-timeout"


class SandboxRuntimeViolation(AgentsError):
    """The spawned sandbox container is not running under the gVisor (``runsc``)
    runtime the isolation model depends on (SEC-M5).

    ``runtime: runsc`` in the host-config is only a *request*; if gVisor is not
    installed/registered the daemon silently falls back to ``runc``, collapsing
    the sandbox to a shared-kernel container. We refuse to run untrusted code
    in that case.
    """

    code = "agents/sandbox-runtime-violation"


class CapabilityMismatch(AgentsError):
    """Tool invocation asked for a capability the key/group cannot serve."""

    code = "capability-mismatch"


class SearchKeyNotConfigured(AgentsError):
    """R12.10 — no active search key attached to the project."""

    code = "tool_unavailable/search_key_not_configured"


class SearchQuotaExceeded(AgentsError):
    """R12.14 — per-project token bucket exhausted."""

    code = "tool_quota_exceeded/search"


class WorkspaceQuotaExceeded(AgentsError):
    """Per-agent workspace file quota exhausted."""

    code = "agents/workspace-quota-exceeded"


class WorkspaceFileNotFound(AgentsError):
    code = "agents/workspace-file-not-found"


__all__ = [
    "A2AForbidden",
    "AgentCapExceeded",
    "AgentNameTaken",
    "AgentNotFound",
    "AgentToolNotFound",
    "AgentToolTypeImmutable",
    "AgentVersionMismatch",
    "AgentsError",
    "CapabilityMismatch",
    "GraphRagConfigOutOfProject",
    "KeyGroupNoMatchingProvider",
    "KeyGroupOutOfProject",
    "McpBindingNotFound",
    "McpEgressDenied",
    "McpTestFailed",
    "McpTimeout",
    "RagConfigOutOfProject",
    "SandboxRuntimeViolation",
    "SearchKeyNotConfigured",
    "SearchQuotaExceeded",
    "FileSearchNeedsKnowledge",
    "ToolNotAvailable",
    "WorkspaceFileNotFound",
    "WorkspaceQuotaExceeded",
]
