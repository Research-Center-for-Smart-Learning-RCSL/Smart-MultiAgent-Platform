"""Agents domain errors → RFC 7807 registration."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.agents.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

_MAP: dict[type[errors.AgentsError], tuple[str, int, str]] = {
    errors.AgentNotFound: ("agents/not-found", 404, "Agent not found"),
    errors.AgentNameTaken: ("agents/name-taken", 409, "Agent name already in use"),
    errors.AgentVersionMismatch: (
        "agents/version-mismatch", 412, "Version mismatch (If-Match)",
    ),
    errors.AgentCapExceeded: (
        "agent-cap-exceeded", 409, "Agent cap (1000 per project) exceeded",
    ),
    errors.KeyGroupOutOfProject: (
        "agents/key-group-out-of-project", 422,
        "Key Group does not belong to the agent's project",
    ),
    errors.A2AForbidden: ("a2a-forbidden", 403, "A2A call forbidden"),
    errors.McpBindingNotFound: (
        "agents/mcp-binding-not-found", 404, "MCP binding not found",
    ),
    errors.McpTestFailed: (
        "agents/mcp-test-failed", 502, "MCP server test failed",
    ),
    errors.McpEgressDenied: (
        "mcp-egress-denied", 403,
        "Egress Proxy blocked this outbound hostname",
    ),
    errors.McpTimeout: (
        "mcp-timeout", 504, "MCP probe exceeded timeout",
    ),
    errors.CapabilityMismatch: (
        "capability-mismatch", 422,
        "Key / group capability does not match the requested tool",
    ),
    errors.SearchKeyNotConfigured: (
        "tool_unavailable/search_key_not_configured", 422,
        "No active search key is configured for this project",
    ),
    errors.SearchQuotaExceeded: (
        "tool_quota_exceeded/search", 429,
        "Per-project web_search rate limit exceeded",
    ),
}


async def _handler(request: Request, exc: errors.AgentsError) -> JSONResponse:
    slug, status, title = _MAP.get(
        type(exc), ("agents/generic", 400, "Agents error"),
    )
    problem = Problem(
        type=problem_type(slug), title=title, status=status, detail=str(exc),
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.AgentsError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
