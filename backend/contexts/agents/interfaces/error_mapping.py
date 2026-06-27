"""Agents domain errors → RFC 7807 registration.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3).
"""

from __future__ import annotations

from fastapi import FastAPI

from contexts.agents.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.AgentNotFound: ("agents/not-found", 404, "Agent not found"),
    errors.AgentNameTaken: ("agents/name-taken", 409, "Agent name already in use"),
    errors.AgentVersionMismatch: (
        "agents/version-mismatch",
        412,
        "Version mismatch (If-Match)",
    ),
    errors.AgentCapExceeded: (
        "agent-cap-exceeded",
        409,
        "Agent cap (1000 per project) exceeded",
    ),
    errors.KeyGroupOutOfProject: (
        "agents/key-group-out-of-project",
        422,
        "Key Group does not belong to the agent's project",
    ),
    errors.KeyGroupNoMatchingProvider: (
        "agents/key-group-no-matching-provider",
        422,
        "Key Group has no keys matching the agent's model provider",
    ),
    errors.RagConfigOutOfProject: (
        "agents/rag-config-not-found",
        404,
        "RAG config not found",
    ),
    errors.GraphRagConfigOutOfProject: (
        "agents/graphrag-config-not-found",
        404,
        "GraphRAG config not found",
    ),
    errors.A2AForbidden: ("a2a-forbidden", 403, "A2A call forbidden"),
    errors.McpBindingNotFound: (
        "agents/mcp-binding-not-found",
        404,
        "MCP binding not found",
    ),
    errors.AgentToolNotFound: (
        "agents/tool-not-found",
        404,
        "Agent tool not found",
    ),
    errors.AgentToolTypeImmutable: (
        "agents/tool-type-immutable",
        409,
        "Singleton tool types are auto-provisioned and cannot be created directly",
    ),
    errors.ToolNotAvailable: (
        "agents/tool-not-available",
        422,
        "This tool type is not available yet",
    ),
    errors.FileSearchNeedsKnowledge: (
        "agents/file-search-needs-knowledge-source",
        422,
        "File Search requires a knowledge source (RAG config) attached to the agent",
    ),
    errors.McpTestFailed: (
        "agents/mcp-test-failed",
        502,
        "MCP server test failed",
    ),
    errors.McpEgressDenied: (
        "mcp-egress-denied",
        403,
        "Egress Proxy blocked this outbound hostname",
    ),
    errors.McpTimeout: (
        "mcp-timeout",
        504,
        "MCP probe exceeded timeout",
    ),
    errors.SandboxRuntimeViolation: (
        "agents/sandbox-runtime-violation",
        503,
        "Sandbox runtime unavailable (gVisor isolation could not be confirmed)",
    ),
    errors.CapabilityMismatch: (
        "capability-mismatch",
        422,
        "Key / group capability does not match the requested tool",
    ),
    errors.SearchKeyNotConfigured: (
        "tool_unavailable/search_key_not_configured",
        422,
        "No active search key is configured for this project",
    ),
    errors.SearchQuotaExceeded: (
        "tool_quota_exceeded/search",
        429,
        "Per-project web_search rate limit exceeded",
    ),
    errors.WorkspaceQuotaExceeded: (
        "agents/workspace-quota-exceeded",
        413,
        "Per-agent workspace file quota exceeded",
    ),
    errors.WorkspaceFileNotFound: (
        "agents/workspace-file-not-found",
        404,
        "Workspace file not found",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.AgentsError, _MAP)


__all__ = ["register"]
