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
    errors.AgentPackNotFound: (
        "agents/example-pack-not-found",
        404,
        "No shipped agent pack with that key",
    ),
    errors.RagConfigOutOfProject: (
        "agents/rag-config-not-found",
        404,
        "RAG config not found",
    ),
    errors.KnowmapConfigOutOfProject: (
        "agents/knowmap-config-out-of-project",
        404,
        "Knowledge Map config not found",
    ),
    errors.KnowmapBuilderKeyGroupConflict: (
        "agents/knowmap-builder-key-group-conflict",
        422,
        "Agent's Key Group must differ from its Knowledge Map's builder Key Group",
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
    errors.AgentConfigTooLarge: (
        "agents/config-too-large",
        413,
        "Merged agent config exceeds the size limit for a config column",
    ),
    errors.WorkflowCapabilitiesInvalid: (
        "agents/invalid-workflow-capabilities",
        422,
        "Merged workflow capabilities are inconsistent",
    ),
    errors.PromptRevisionNotFound: (
        "agents/prompt-revision-not-found",
        404,
        "Prompt revision not found",
    ),
    errors.DesignRunNotFound: ("agents/design-run-not-found", 404, "Design run not found"),
    errors.DesignAgentOutOfProject: ("agents/design-agent-not-found", 404, "Agent not found"),
    errors.DesignTargetsInvalid: (
        "agents/design-targets-invalid",
        422,
        "The teacher and peer agents must be different agents",
    ),
    errors.DesignRunInFlight: (
        "agents/design-run-in-flight",
        409,
        "A design run for this project is already in progress",
    ),
    errors.DesignRunNotApplicable: (
        "agents/design-run-not-applicable",
        409,
        "This design run has no proposal to apply",
    ),
    errors.DesignApplyConflict: (
        "agents/design-apply-conflict",
        409,
        "An agent changed since this design was drafted",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.AgentsError, _MAP)


__all__ = ["register"]
