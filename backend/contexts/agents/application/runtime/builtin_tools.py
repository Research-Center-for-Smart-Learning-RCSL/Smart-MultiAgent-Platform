"""Built-in + MCP tool assembly for the turn registry (K.5).

K.2's ``tool_registry`` deliberately stayed infra-free and left ``web_search`` /
``file`` / ``code_exec`` / MCP as ``extra`` tools "injected by their own wiring
(sandbox lands in K.5)". This module IS that wiring — the first production caller
of the orphaned factories ``egress_proxy_client_from_settings`` (web-search
egress), ``search_adapters.build_registry`` (provider map), and
``docker_runsc_sandbox_from_settings`` (the gVisor runner).

It turns the existing built-in tool façades (``WebSearchTool`` / ``FileTool`` /
``CodeExecTool``) and the agent's MCP bindings into uniform ``Tool`` objects the
turn loop dispatches. Each tool's ``invoke`` returns a ``ToolResult`` and never
raises into the loop — a missing search key or a sandbox fault surfaces as an
``is_error`` result the model can read, not a turn-aborting exception.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.runtime.tool_registry import Tool, ToolResult
from contexts.agents.domain.mcp import SearchResult
from contexts.agents.domain.models import Agent, McpBinding

# Per-tool output caps so a chatty tool can't blow the context window.
_MAX_TOOL_OUTPUT = 16_000


@dataclass(frozen=True, slots=True)
class BuiltinToolDeps:
    """Injectable dependencies — production via :func:`default_builtin_deps`,
    fakes in tests."""

    runner: Any  # SandboxRunner
    proxy: Any  # EgressProxyClient
    adapters: dict[Any, Any]  # {SearchProvider: SearchAdapter}
    cache: Any  # SearchCache
    rate_limiter: Any  # SearchRateLimiter


def default_builtin_deps() -> BuiltinToolDeps:
    """Wire the production built-in-tool dependencies (composition root)."""
    from contexts.agents.infrastructure.egress_client import egress_proxy_client_from_settings
    from contexts.agents.infrastructure.sandbox.docker_runsc import (
        docker_runsc_sandbox_from_settings,
    )
    from contexts.agents.infrastructure.search_adapters import build_registry
    from contexts.agents.infrastructure.search_cache import RedisSearchCache
    from contexts.agents.infrastructure.search_rate_limiter import RedisSearchRateLimiter

    return BuiltinToolDeps(
        runner=docker_runsc_sandbox_from_settings(),
        proxy=egress_proxy_client_from_settings(),
        adapters=build_registry(),
        cache=RedisSearchCache(),
        rate_limiter=RedisSearchRateLimiter(),
    )


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_TOOL_OUTPUT else text[:_MAX_TOOL_OUTPUT] + "\n…[truncated]"


# --------------------------------------------------------------------------- #
# Tool schemas                                                                 #
# --------------------------------------------------------------------------- #

_WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search query."},
        "top_k": {"type": "integer", "description": "Max results (1–20)."},
        "freshness": {"enum": ["any", "day", "week", "month", "year"]},
    },
    "required": ["query"],
    "additionalProperties": False,
}

_CODE_EXEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "description": "Python source to run."},
        "stdin": {"type": "string", "description": "Optional stdin."},
    },
    "required": ["source"],
    "additionalProperties": False,
}

_FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {"enum": ["list", "read", "write"]},
        "path": {"type": "string", "description": "Path under /workspace."},
        "content": {"type": "string", "description": "UTF-8 content for write."},
    },
    "required": ["op", "path"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Tool builders                                                                #
# --------------------------------------------------------------------------- #


def _build_web_search_tool(db: AsyncSession, *, agent: Agent, deps: BuiltinToolDeps) -> Tool:
    from contexts.agents.application.tools.web_search import WebSearchTool

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        tool = WebSearchTool(
            agent_id=agent.id,
            project_id=agent.project_id,
            db=db,
            adapters=deps.adapters,
            cache=deps.cache,
            rate_limiter=deps.rate_limiter,
            proxy=deps.proxy,
        )
        try:
            results = await tool.search(
                str(args.get("query", "")),
                top_k=int(args.get("top_k", 5) or 5),
                freshness=str(args.get("freshness", "any")),  # type: ignore[arg-type]
            )
        except Exception as exc:
            return ToolResult(content=f"web_search failed: {exc}", is_error=True)
        return ToolResult(content=_clip(_dump_results(results)))

    return Tool(
        name="web_search",
        description="Search the web (BYO provider) for current information. Returns titles, URLs, snippets.",
        input_schema=_WEB_SEARCH_SCHEMA,
        invoke=_invoke,
    )


def _dump_results(results: list[SearchResult]) -> str:
    return json.dumps(
        [{"title": r.title, "url": r.url, "snippet": r.snippet, "score": r.score} for r in results],
        separators=(",", ":"),
    )


def _build_code_exec_tool(db: AsyncSession, *, agent: Agent, deps: BuiltinToolDeps) -> Tool:
    from contexts.agents.application.tools.code_exec import CodeExecTool

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        source = str(args.get("source", ""))
        if not source:
            return ToolResult(content="code_exec requires 'source'", is_error=True)
        tool = CodeExecTool(agent_id=agent.id, runner=deps.runner, db=db)
        try:
            res = await tool.run(source, stdin=str(args.get("stdin", "")))
        except Exception as exc:
            return ToolResult(content=f"code_exec failed: {exc}", is_error=True)
        body = res.stdout if res.ok else f"{res.stdout}\n[stderr]\n{res.stderr}".strip()
        return ToolResult(content=_clip(body or "(no output)"), is_error=not res.ok)

    return Tool(
        name="code_exec",
        description="Run a short Python snippet in a gVisor sandbox (30s cap). Returns stdout/stderr.",
        input_schema=_CODE_EXEC_SCHEMA,
        invoke=_invoke,
    )


def _build_file_tool(db: AsyncSession, *, agent: Agent, deps: BuiltinToolDeps) -> Tool:
    from contexts.agents.application.tools.file_tool import FileTool

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        op = str(args.get("op", ""))
        path = str(args.get("path", ""))
        tool = FileTool(agent_id=agent.id, runner=deps.runner, db=db)
        try:
            if op == "list":
                # The path guard rejects "/" (only /workspace and below are
                # valid), so an absent/root path lists the workspace root.
                res = await tool.list_(path if path and path != "/" else "/workspace")
            elif op == "read":
                res = await tool.read(path)
            elif op == "write":
                res = await tool.write(path, str(args.get("content", "")).encode("utf-8"))
            else:
                return ToolResult(content=f"unknown file op {op!r}", is_error=True)
        except Exception as exc:
            return ToolResult(content=f"file {op} failed: {exc}", is_error=True)
        return ToolResult(content=_clip(res.stdout or "(ok)"), is_error=not res.ok)

    return Tool(
        name="file",
        description="Read/list/write files in the agent's /workspace volume.",
        input_schema=_FILE_SCHEMA,
        invoke=_invoke,
    )


def _mcp_tool_name(binding: McpBinding, tool: str) -> str:
    """Namespaced so two bindings exposing the same tool name don't collide."""
    return f"mcp__{str(binding.id)[:8]}__{tool}"


def _build_mcp_tool(
    db: AsyncSession,
    *,
    agent: Agent,
    binding: McpBinding,
    tool: str,
    deps: BuiltinToolDeps,
) -> Tool:
    async def _invoke(args: dict[str, Any]) -> ToolResult:
        auth = _unseal_binding_auth(binding)
        try:
            res = await deps.runner.invoke_mcp_tool(
                agent_id=agent.id,
                binding_id=binding.id,
                tool_name=tool,
                arguments=dict(args),
                project_id=agent.project_id,
                source=binding.source.value,
                reference=binding.reference,
                auth=auth,
            )
        except Exception as exc:
            await _audit_mcp_invoke(db, agent, binding, tool, ok=False)
            return ToolResult(content=f"mcp tool {tool} failed: {exc}", is_error=True)
        await _audit_mcp_invoke(db, agent, binding, tool, ok=res.ok)
        body = res.stdout if res.ok else f"{res.stdout}\n[stderr]\n{res.stderr}".strip()
        return ToolResult(content=_clip(body or "(no output)"), is_error=not res.ok)

    return Tool(
        name=_mcp_tool_name(binding, tool),
        description=f"MCP tool '{tool}' from bound server {binding.reference}.",
        input_schema={"type": "object", "additionalProperties": True},
        invoke=_invoke,
    )


async def _audit_mcp_invoke(
    db: AsyncSession, agent: Agent, binding: McpBinding, tool: str, *, ok: bool
) -> None:
    """Per-call MCP tool audit (R12.02/R12.15). Best-effort: an audit hiccup
    must not abort the tool call or the turn."""
    try:
        from shared_kernel import audit

        await audit.emit(
            db,
            audit.AuditEvent(
                action="mcp.tool_invoked",
                resource_type="agent",
                resource_id=agent.id,
                metadata={
                    "binding_id": str(binding.id),
                    "reference": binding.reference,
                    "tool": tool,
                    "ok": ok,
                },
            ),
        )
    except Exception:  # noqa: S110  # pragma: no cover - audit is best-effort
        pass


def _unseal_binding_auth(binding: McpBinding) -> dict[str, Any] | None:
    """Decrypt the binding's sealed auth for the sandbox, best-effort.

    The plaintext is handed only to the gVisor container (env), never logged.
    Mirrors ``McpBindingService.test``'s unseal path."""
    sealed = binding.config.get("auth") if isinstance(binding.config, dict) else None
    if not sealed:
        return None
    try:
        from contexts.agents.application.mcp_service import unseal_auth

        return unseal_auth(binding.id, sealed)
    except Exception:
        return None


def build_builtin_tools(
    db: AsyncSession,
    *,
    agent: Agent,
    mcp_bindings: list[McpBinding],
    deps: BuiltinToolDeps,
) -> list[Tool]:
    """Assemble web_search + code_exec + file + per-binding MCP tools (K.5)."""
    tools: list[Tool] = [
        _build_web_search_tool(db, agent=agent, deps=deps),
        _build_code_exec_tool(db, agent=agent, deps=deps),
        _build_file_tool(db, agent=agent, deps=deps),
    ]
    for binding in mcp_bindings:
        for tool_name in binding.allowed_tools:
            tools.append(_build_mcp_tool(db, agent=agent, binding=binding, tool=tool_name, deps=deps))
    return tools


__all__ = ["BuiltinToolDeps", "build_builtin_tools", "default_builtin_deps"]
