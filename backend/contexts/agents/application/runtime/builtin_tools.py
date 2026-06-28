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
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.runtime.tool_registry import Tool, ToolResult
from contexts.agents.domain.mcp import SearchResult
from contexts.agents.domain.models import Agent, AgentTool, AgentToolType

logger = logging.getLogger(__name__)

# Per-tool output caps so a chatty tool can't blow the context window.
_MAX_TOOL_OUTPUT = 16_000


class RagProviderProto(Protocol):
    async def query(
        self,
        *,
        rag_config_id: uuid.UUID | None,
        query_text: str | None,
        agent_id: uuid.UUID | None = ...,
        top_k: int | None = ...,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class BuiltinToolDeps:
    """Injectable dependencies — production via :func:`default_builtin_deps`,
    fakes in tests."""

    runner: Any  # SandboxRunner
    proxy: Any  # EgressProxyClient
    adapters: dict[Any, Any]  # {SearchProvider: SearchAdapter}
    cache: Any  # SearchCache
    rate_limiter: Any  # SearchRateLimiter
    rag_provider: RagProviderProto | None = None


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


def _build_code_exec_tool(
    db: AsyncSession,
    *,
    agent: Agent,
    deps: BuiltinToolDeps,
    chatroom_id: uuid.UUID | None = None,
    artifact_sink: list[dict[str, Any]] | None = None,
) -> Tool:
    from contexts.agents.application.tools.code_exec import CodeExecTool

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        source = str(args.get("source", ""))
        if not source:
            return ToolResult(content="code_exec requires 'source'", is_error=True)
        tool = CodeExecTool(agent_id=agent.id, runner=deps.runner, db=db, chatroom_id=chatroom_id)
        try:
            res = await tool.run(source, stdin=str(args.get("stdin", "")))
        except Exception as exc:
            return ToolResult(content=f"code_exec failed: {exc}", is_error=True)
        meta = res.metadata if isinstance(res.metadata, dict) else {}
        # Collect any artifacts the kernel produced (charts/files) so the turn
        # engine can attach them to the agent's reply. Best-effort, never raises.
        if artifact_sink is not None:
            produced = meta.get("artifacts")
            if isinstance(produced, list):
                artifact_sink.extend(a for a in produced if isinstance(a, dict))
        body = res.stdout if res.ok else f"{res.stdout}\n[stderr]\n{res.stderr}".strip()
        body = _clip(body or "(no output)")
        # Surface a kernel restart (state loss) to the model from the structured
        # metadata flag rather than relying on a magic string in stdout.
        if meta.get("restarted"):
            body = "[kernel restarted: in-memory state was lost]\n" + body
        return ToolResult(content=body, is_error=not res.ok)

    return Tool(
        name="code_exec",
        description="Run a Python snippet in a gVisor sandbox (30s cap). State persists across "
        "calls in a chat; loaded data and saved files survive. Returns stdout/stderr.",
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


_FILE_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What to search the agent's files for."},
        "top_k": {"type": "integer", "description": "Max passages to return (1-20)."},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def _build_file_search_tool(
    db: AsyncSession, *, agent: Agent, deps: BuiltinToolDeps, config: dict[str, Any]
) -> Tool:
    async def _invoke(args: dict[str, Any]) -> ToolResult:
        if agent.rag_config_id is None:
            return ToolResult(
                content="file_search unavailable: no knowledge source configured for this agent.",
                is_error=True,
            )
        if deps.rag_provider is None:
            return ToolResult(content="file_search unavailable: RAG provider not configured.", is_error=True)
        default_top_k = config.get("top_k", 8)
        top_k = min(max(int(args.get("top_k") or default_top_k), 1), 20)
        try:
            ctx = await deps.rag_provider.query(
                rag_config_id=agent.rag_config_id,
                query_text=str(args.get("query", "")),
                agent_id=agent.id,
                top_k=top_k,
            )
        except Exception as exc:
            return ToolResult(content=f"file_search failed: {exc}", is_error=True)
        if ctx is None or not ctx.sources or not getattr(ctx, "block", None):
            return ToolResult(content="No matching passages found in the agent's files.")
        return ToolResult(content=_clip(_format_passages(ctx)))

    return Tool(
        name="file_search",
        description="Search files uploaded for this agent and return relevant passages with citations.",
        input_schema=_FILE_SEARCH_SCHEMA,
        invoke=_invoke,
    )


def _format_passages(ctx: Any) -> str:
    """Render RAG context as numbered passages the model can cite."""
    lines: list[str] = []
    for i, src in enumerate(ctx.sources, 1):
        filename = src.get("filename") or "unknown"
        score = src.get("score", 0)
        lines.append(f"[{i}] {filename} (score: {score})")
    lines.append("")
    lines.append(ctx.block)
    return "\n".join(lines)


AgentToolDeps = BuiltinToolDeps

_FUNCTION_RATE_LIMIT_PER_MINUTE = 60


def _unseal_tool_auth_safe(tool: AgentTool) -> dict[str, Any] | None:
    sealed = tool.config.get("auth") if isinstance(tool.config, dict) else None
    if not sealed or not isinstance(sealed, dict) or not sealed.get("__sealed__"):
        return None
    try:
        from contexts.agents.application.tool_auth import unseal_tool_auth

        return unseal_tool_auth(tool.id, sealed)
    except Exception:
        logger.error("Failed to unseal tool auth for %s", tool.id, exc_info=True)
        return None


_ALLOWED_UPSTREAM_AUTH_NAMES = frozenset({"authorization", "x-api-key", "x-auth-token"})


def _auth_pair(auth: dict[str, Any] | None) -> tuple[str, str] | None:
    if not auth:
        return None
    auth_type = auth.get("type")
    if auth_type == "bearer":
        token = auth.get("token", "")
        return ("Authorization", f"Bearer {token}") if token else None
    if auth_type == "header":
        name = auth.get("name", "")
        value = auth.get("value", "")
        if not name or not value:
            return None
        if name.lower() not in _ALLOWED_UPSTREAM_AUTH_NAMES:
            logger.warning("function auth header name %r not in allowed set, skipping", name)
            return None
        return (name, value)
    return None


async def _audit_tool_invoke(
    db: AsyncSession, agent: Agent, tool: AgentTool, mcp_tool: str, *, ok: bool
) -> None:
    try:
        from shared_kernel import audit

        await audit.emit(
            db,
            audit.AuditEvent(
                action="mcp.tool_invoked",
                resource_type="agent",
                resource_id=agent.id,
                metadata={
                    "tool_id": str(tool.id),
                    "reference": tool.mcp_reference(),
                    "tool": mcp_tool,
                    "ok": ok,
                },
            ),
        )
    except Exception:  # pragma: no cover
        logger.warning("Failed to write tool audit event", exc_info=True)


def _mcp_tool_name_from_agent_tool(tool: AgentTool, mcp_tool: str) -> str:
    return f"mcp__{str(tool.id)[:8]}__{mcp_tool}"


def _build_mcp_tool_from_agent_tool(
    db: AsyncSession,
    *,
    agent: Agent,
    tool: AgentTool,
    mcp_tool: str,
    deps: BuiltinToolDeps,
) -> Tool:
    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from contexts.agents.application.tool_auth import unseal_tool_auth

        sealed = tool.config.get("auth") if isinstance(tool.config, dict) else None
        auth = None
        if sealed and isinstance(sealed, dict) and sealed.get("__sealed__"):
            try:
                auth = unseal_tool_auth(tool.id, sealed)
            except Exception:
                # Fail closed: the binding has stored credentials, so never fall back
                # to an unauthenticated call (the upstream could treat that as a
                # different identity). Surface an error instead.
                logger.error("Failed to unseal tool auth", exc_info=True)
                await _audit_tool_invoke(db, agent, tool, mcp_tool, ok=False)
                return ToolResult(
                    content=f"mcp tool {mcp_tool} failed: stored credentials could not be unsealed",
                    is_error=True,
                )
        try:
            res = await deps.runner.invoke_mcp_tool(
                agent_id=agent.id,
                binding_id=tool.id,
                tool_name=mcp_tool,
                arguments=dict(args),
                project_id=agent.project_id,
                source=tool.mcp_source(),
                reference=tool.mcp_reference(),
                auth=auth,
            )
        except Exception as exc:
            await _audit_tool_invoke(db, agent, tool, mcp_tool, ok=False)
            return ToolResult(content=f"mcp tool {mcp_tool} failed: {exc}", is_error=True)
        await _audit_tool_invoke(db, agent, tool, mcp_tool, ok=res.ok)
        body = res.stdout if res.ok else f"{res.stdout}\n[stderr]\n{res.stderr}".strip()
        return ToolResult(content=_clip(body or "(no output)"), is_error=not res.ok)

    return Tool(
        name=_mcp_tool_name_from_agent_tool(tool, mcp_tool),
        description=f"MCP tool '{mcp_tool}' from bound server {tool.mcp_reference()}.",
        input_schema={"type": "object", "additionalProperties": True},
        invoke=_invoke,
    )


def _build_function_tool(
    db: AsyncSession,
    *,
    agent: Agent,
    tool: AgentTool,
    deps: BuiltinToolDeps,
) -> Tool:
    cfg = tool.config
    http_cfg = cfg.get("http", {})
    fn_name = str(cfg.get("name", tool.display_name or f"fn_{str(tool.id)[:8]}"))
    fn_desc = str(cfg.get("description", ""))
    fn_params = cfg.get("parameters", {"type": "object", "additionalProperties": True})

    async def _invoke(args: dict[str, Any]) -> ToolResult:
        from urllib.parse import urlsplit

        from contexts.agents.domain.errors import McpEgressDenied
        from contexts.agents.infrastructure.mcp_repositories import EgressAllowlistRepository

        url = str(http_cfg.get("url", ""))
        host = (urlsplit(url).hostname or "").lower()
        if not await EgressAllowlistRepository(db).is_allowed(
            project_id=agent.project_id,
            hostname=host,
        ):
            return ToolResult(
                content=f"function blocked: host {host} is not on the project egress allowlist.",
                is_error=True,
            )

        # Rate limit (fixed-window, same pattern as web search).
        try:
            import time as _time

            from shared_kernel.auth.clients import get_redis

            redis = get_redis()
            window = int(_time.time()) // 60
            rl_key = f"function:rl:{agent.project_id}:{window}"
            pipe = redis.pipeline(transaction=False)
            pipe.incr(rl_key, 1)
            pipe.expire(rl_key, 70)
            rl_results = await pipe.execute()
            if int(rl_results[0]) > _FUNCTION_RATE_LIMIT_PER_MINUTE:
                return ToolResult(content="function rate limit exceeded (60/min/project).", is_error=True)
        except Exception:
            logger.warning("function rate-limiter unavailable, blocking call", exc_info=True)
            return ToolResult(
                content="function call temporarily unavailable (rate-limiter offline).",
                is_error=True,
            )

        headers = dict(http_cfg.get("headers") or {})
        sealed_auth = cfg.get("auth") if isinstance(cfg, dict) else None
        auth = _unseal_tool_auth_safe(tool)
        if (
            isinstance(sealed_auth, dict)
            and sealed_auth.get("__sealed__")
            and auth is None
        ):
            # Fail closed: stored credentials exist but could not be unsealed.
            return ToolResult(
                content="function blocked: stored credentials could not be unsealed.",
                is_error=True,
            )
        upstream_auth = _auth_pair(auth)
        method = str(http_cfg.get("method", "GET")).upper()

        try:
            if method in ("GET", "DELETE"):
                status, _h, body = await deps.proxy.request(
                    method=method,
                    url=url,
                    project_id=agent.project_id,
                    headers=headers,
                    params=dict(args),
                    upstream_auth=upstream_auth,
                    timeout_s=30.0,
                )
            else:
                status, _h, body = await deps.proxy.request(
                    method=method,
                    url=url,
                    project_id=agent.project_id,
                    headers=headers,
                    json_body=dict(args),
                    upstream_auth=upstream_auth,
                    timeout_s=30.0,
                )
        except McpEgressDenied:
            await _audit_tool_invoke(db, agent, tool, fn_name, ok=False)
            return ToolResult(content="function blocked by egress policy.", is_error=True)
        except Exception as exc:
            await _audit_tool_invoke(db, agent, tool, fn_name, ok=False)
            return ToolResult(content=f"function call failed: {exc}", is_error=True)

        ok = 200 <= status < 400
        await _audit_tool_invoke(db, agent, tool, fn_name, ok=ok)
        text = body.decode("utf-8", "replace")
        return ToolResult(content=_clip(f"HTTP {status}\n{text}"), is_error=not ok)

    return Tool(name=fn_name, description=fn_desc, input_schema=fn_params, invoke=_invoke)


def build_agent_tools(
    db: AsyncSession,
    *,
    agent: Agent,
    tools: list[AgentTool],
    deps: BuiltinToolDeps,
    chatroom_id: uuid.UUID | None = None,
    artifact_sink: list[dict[str, Any]] | None = None,
) -> list[Tool]:
    """Assemble the agent's enabled tools from the unified ``agent_tools`` table.

    Dispatches by ``tool_type``; disabled rows and ``local_shell`` are skipped.
    """
    out: list[Tool] = []
    for t in tools:
        if not t.enabled:
            continue
        match t.tool_type:
            case AgentToolType.HOSTED_WEB_SEARCH:
                out.append(_build_web_search_tool(db, agent=agent, deps=deps))
            case AgentToolType.HOSTED_CODE_INTERPRETER:
                out.append(
                    _build_code_exec_tool(
                        db,
                        agent=agent,
                        deps=deps,
                        chatroom_id=chatroom_id,
                        artifact_sink=artifact_sink,
                    )
                )
            case AgentToolType.HOSTED_FILE_WORKSPACE:
                out.append(_build_file_tool(db, agent=agent, deps=deps))
            case AgentToolType.HOSTED_FILE_SEARCH:
                out.append(_build_file_search_tool(db, agent=agent, deps=deps, config=t.config))
            case AgentToolType.HOSTED_MCP:
                for mcp_tool_name in t.mcp_allowed_tools():
                    out.append(
                        _build_mcp_tool_from_agent_tool(
                            db,
                            agent=agent,
                            tool=t,
                            mcp_tool=mcp_tool_name,
                            deps=deps,
                        )
                    )
            case AgentToolType.LOCAL_FUNCTION:
                out.append(_build_function_tool(db, agent=agent, tool=t, deps=deps))
            case AgentToolType.LOCAL_SHELL:
                continue
    return out


__all__ = [
    "AgentToolDeps",
    "BuiltinToolDeps",
    "build_agent_tools",
    "default_builtin_deps",
]
