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
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.runtime.tool_registry import (
    ARTIFACT_SKIP_KEY,
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_TOTAL_BYTES,
    MAX_ARTIFACTS_PER_TURN,
    Tool,
    ToolResult,
    artifact_size_bytes,
    clip_tool_output,
)
from contexts.agents.domain.mcp import SearchResult
from contexts.agents.domain.models import Agent, AgentTool, AgentToolType

logger = logging.getLogger(__name__)


class RagProviderProto(Protocol):
    async def query(
        self,
        *,
        rag_config_id: uuid.UUID | None,
        query_text: str | None = ...,
        query_texts: Sequence[str] | None = ...,
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
        "path": {
            "type": "string",
            "description": "Path under /workspace. Bare names are resolved against /workspace, "
            "not against code_exec's working directory.",
        },
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
        return ToolResult(content=clip_tool_output(_dump_results(results)))

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

    # One per built tool, i.e. one per turn: the ceilings in G.3 are per-turn,
    # so the running totals have to outlive a single call. Held here rather than
    # re-derived from `artifact_sink` on every call, which was quadratic.
    budget = _ArtifactBudget()

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
                fresh = [a for a in produced if isinstance(a, dict)]
                if chatroom_id is not None:
                    await _hydrate_oversized(
                        deps.runner,
                        agent_id=agent.id,
                        chatroom_id=chatroom_id,
                        artifacts=fresh,
                        budget=budget,
                    )
                artifact_sink.extend(fresh)
        body = res.stdout if res.ok else f"{res.stdout}\n[stderr]\n{res.stderr}".strip()
        # Built after hydration, so it can read the skip marks that records (G.4),
        # and appended by `clip_tool_output` itself so the bound stays that
        # function's guarantee rather than this caller's.
        body = clip_tool_output(body or "(no output)", suffix=_artifact_note(meta.get("artifacts")))
        # Surface a kernel restart (state loss) to the model from the structured
        # metadata flag rather than relying on a magic string in stdout.
        if meta.get("restarted"):
            body = "[kernel restarted: in-memory state was lost]\n" + body
        return ToolResult(content=body, is_error=not res.ok)

    return Tool(
        name="code_exec",
        # The working directory is the whole contract between this tool and `file`:
        # both mount one volume at /workspace, but the kernel chdirs into the
        # per-chat session dir, so a bare name means different files in each tool.
        # The description is the only place the model can learn that.
        description="Run a Python snippet in a gVisor sandbox (30s cap). State persists across "
        "calls in a chat; loaded data and saved files survive. Your working directory is this "
        "chat's own session directory: `inputs/` holds files from the conversation, and anything "
        "you save to `outputs/` up to 32 MB is returned as an artifact, and the tool result tells "
        "you which files came back. The agent's persistent volume - the "
        "same files the `file` tool sees - is mounted read-only at /workspace; read it by absolute "
        "path (e.g. /workspace/notes.md), and use the `file` tool to write there. Returns "
        "stdout/stderr.",
        input_schema=_CODE_EXEC_SCHEMA,
        invoke=_invoke,
    )


@dataclass(slots=True)
class _ArtifactBudget:
    """Running per-turn artifact totals, carried across `code_exec` calls.

    The ceilings are per-turn (G.3), so the counters have to outlive one call.
    """

    seen: set[str] = field(default_factory=set)
    fetched: int = 0


async def _hydrate_oversized(
    runner: Any,
    *,
    agent_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    artifacts: list[dict[str, Any]],
    budget: _ArtifactBudget,
) -> None:
    """Pull artifacts too large to inline off the kernel, here and now.

    Runs at the exec reply rather than at turn end because the kernel can be
    evicted in between; see docs/agent-tools/G-artifact-transport.md G.2. Bytes
    ride on the descriptor as ``data``; anything not delivered is marked with
    ``ARTIFACT_SKIP_KEY`` so the model's note and the operator log both see it
    (G.4). Never raises: an artifact must not cost the turn its reply.
    """
    for art in artifacts:
        if art.get("b64"):
            continue
        rel = str(art.get("rel_path") or "")
        if not rel or rel in budget.seen:
            # `_persist_artifacts` dedups on rel_path and keeps the first, so
            # fetching a repeat would move up to 32 MB only to discard it.
            continue
        budget.seen.add(rel)
        size = artifact_size_bytes(art)
        name = art.get("filename")
        if size > MAX_ARTIFACT_BYTES:
            # Distinguished from the budget case below: this file could never be
            # delivered at any point in the turn, so the model should shrink it
            # rather than retry.
            art[ARTIFACT_SKIP_KEY] = "too_large"
            logger.warning("artifact %s is %d bytes, above the %d limit", name, size, MAX_ARTIFACT_BYTES)
            continue
        # Checked before the fetch, not after: the point is not to notice the
        # flood but to never move the bytes.
        if len(budget.seen) > MAX_ARTIFACTS_PER_TURN or budget.fetched + size > MAX_ARTIFACT_TOTAL_BYTES:
            art[ARTIFACT_SKIP_KEY] = "budget"
            logger.warning(
                "artifact budget reached for room %s: not fetching %s (%d bytes)", chatroom_id, name, size
            )
            continue
        try:
            data = await runner.fetch_kernel_artifact(
                agent_id=agent_id,
                chatroom_id=chatroom_id,
                path=rel,
                max_bytes=MAX_ARTIFACT_BYTES,
            )
        except Exception:
            logger.warning("artifact fetch raised for %s", rel, exc_info=True)
            data = None
        if data is None:
            art[ARTIFACT_SKIP_KEY] = "unavailable"
            continue
        art["data"] = data
        budget.fetched += len(data)


def _artifact_note(produced: Any) -> str:
    """What this call wrote to ``outputs/``, and what will not be coming back.

    Says *written*, never *returned*: delivery happens later and can still fail,
    and platform text asserting an outcome is the confabulation this note exists
    to prevent. Reads the skip marks `_hydrate_oversized` left, so a file the
    per-turn budget refused is never listed as though it were on its way (G.4).

    Advisory, so it must never raise: it annotates a result it must not be able
    to fail.
    """
    from shared_kernel.storage.sanitize import safe_input_name

    if not isinstance(produced, list):
        return ""
    arts = [a for a in produced if isinstance(a, dict)]
    written: list[str] = []
    too_large: list[str] = []
    withheld: list[str] = []
    # Only the displayed slice is named; the count below covers the remainder,
    # so the model never reads a complete-looking list that is not complete.
    for art in arts[:MAX_ARTIFACTS_PER_TURN]:
        name = safe_input_name(str(art.get("filename") or "artifact"))
        size = artifact_size_bytes(art)
        reason = art.get(ARTIFACT_SKIP_KEY)
        if reason == "too_large" or size > MAX_ARTIFACT_BYTES:
            too_large.append(f"{name} ({size} bytes)")
        elif reason:
            withheld.append(name)
        else:
            written.append(name)
    parts = []
    if written:
        parts.append(f"[wrote to outputs/: {', '.join(written)}]")
    if withheld:
        parts.append(
            f"[not returned, this turn's artifact budget is used up: {', '.join(withheld)}. "
            "They remain in outputs/ inside the sandbox.]"
        )
    if too_large:
        parts.append(
            f"[too large to return, over the {MAX_ARTIFACT_BYTES} byte limit: "
            f"{', '.join(too_large)}. The file is still in outputs/ inside the sandbox; "
            "write a smaller one if the user needs it.]"
        )
    overflow = len(arts) - MAX_ARTIFACTS_PER_TURN
    if overflow > 0:
        parts.append(f"[and {overflow} further artifact(s) not listed here.]")
    # Leading newline so the caller can append without knowing whether a note
    # is present; `clip_tool_output` does the appending.
    return "\n" + "\n".join(parts) if parts else ""


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
        return ToolResult(content=clip_tool_output(res.stdout or "(ok)"), is_error=not res.ok)

    return Tool(
        name="file",
        # Deliberately does not promise what `list` returns: op=list executes inside
        # smap/mcp-runtime, which is tag-pinned and deploys independently of this
        # backend. Against a stale image the listing is still bare names, and a
        # description that asserted otherwise would be actively wrong there.
        description="Read/list/write files in the agent's /workspace volume. Paths are rooted at "
        "/workspace. When handing a path to `code_exec`, use the absolute /workspace/... form - "
        "its working directory is elsewhere, so a bare name will not resolve there.",
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
        return ToolResult(content=clip_tool_output(_format_passages(ctx)))

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


def resolve_tool_auth(tool: AgentTool) -> tuple[dict[str, Any] | None, bool]:
    """Resolve a tool's upstream auth, distinguishing 'no auth' from 'unsealable'.

    Returns ``(auth, unsealable)``. ``unsealable`` is True when the tool stores
    sealed credentials that could not be decrypted — callers MUST fail closed
    (surface an error) rather than fall back to an unauthenticated call, which
    the upstream could treat as a different identity.
    """
    sealed = tool.config.get("auth") if isinstance(tool.config, dict) else None
    auth = _unseal_tool_auth_safe(tool)
    unsealable = bool(isinstance(sealed, dict) and sealed.get("__sealed__") and auth is None)
    return auth, unsealable


async def function_egress_allowed(db: AsyncSession, *, project_id: uuid.UUID, url: str) -> tuple[str, bool]:
    """Return ``(host, allowed)`` for a function URL against the egress allowlist.

    Shared by the live function invocation and the reachability probe so the two
    never disagree on host normalization or allowlist semantics.
    """
    from urllib.parse import urlsplit

    from contexts.agents.infrastructure.mcp_repositories import EgressAllowlistRepository

    host = (urlsplit(url).hostname or "").lower()
    allowed = await EgressAllowlistRepository(db).is_allowed(project_id=project_id, hostname=host)
    return host, allowed


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
        auth, unsealable = resolve_tool_auth(tool)
        if unsealable:
            # Fail closed: stored credentials exist but could not be unsealed.
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
        return ToolResult(content=clip_tool_output(body or "(no output)"), is_error=not res.ok)

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
        from contexts.agents.application.egress import (
            EgressBlocked,
            perform_egress_request,
        )
        from contexts.agents.domain.errors import McpEgressDenied

        url = str(http_cfg.get("url", ""))
        headers = dict(http_cfg.get("headers") or {})
        auth, unsealable = resolve_tool_auth(tool)
        if unsealable:
            # Fail closed: stored credentials exist but could not be unsealed.
            await _audit_tool_invoke(db, agent, tool, fn_name, ok=False)
            return ToolResult(
                content="function blocked: stored credentials could not be unsealed.",
                is_error=True,
            )
        upstream_auth = _auth_pair(auth)
        method = str(http_cfg.get("method", "GET")).upper()

        # Allowlist + fixed-window rate limit + egress-proxy call now live in the
        # shared perform_egress_request (SoC seam); this path keeps its own auth
        # resolution, audit, and message strings.
        try:
            outcome = await perform_egress_request(
                db,
                project_id=agent.project_id,
                method=method,
                url=url,
                proxy=deps.proxy,
                args=dict(args),
                headers=headers,
                upstream_auth=upstream_auth,
                timeout_s=30.0,
                rate_limit_prefix="function",
                rate_limit_per_minute=_FUNCTION_RATE_LIMIT_PER_MINUTE,
            )
        except EgressBlocked as blocked:
            if blocked.kind == "allowlist":
                return ToolResult(
                    content=f"function blocked: host {blocked.host} is not on the project egress allowlist.",
                    is_error=True,
                )
            if blocked.kind == "rate_limit":
                return ToolResult(content="function rate limit exceeded (60/min/project).", is_error=True)
            logger.warning("function rate-limiter unavailable, blocking call", exc_info=True)
            return ToolResult(
                content="function call temporarily unavailable (rate-limiter offline).",
                is_error=True,
            )
        except McpEgressDenied:
            await _audit_tool_invoke(db, agent, tool, fn_name, ok=False)
            return ToolResult(content="function blocked by egress policy.", is_error=True)
        except Exception as exc:
            await _audit_tool_invoke(db, agent, tool, fn_name, ok=False)
            return ToolResult(content=f"function call failed: {exc}", is_error=True)

        ok = outcome.ok
        await _audit_tool_invoke(db, agent, tool, fn_name, ok=ok)
        if not ok and 300 <= outcome.status < 400:
            location = outcome.location
            detail = f"redirect to {location}" if location else "redirect with no Location header"
            content = (
                f"HTTP {outcome.status} — {detail}; retry with that URL if the host is on the allowlist."
            )
        else:
            text = outcome.body.decode("utf-8", "replace")
            content = f"HTTP {outcome.status}\n{text}"
        return ToolResult(content=clip_tool_output(content), is_error=not ok)

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
    # User LOCAL_FUNCTION tools are collected separately and appended LAST, so the
    # registry's first-registration-wins backstop always keeps a built-in over a
    # same-named user function even if the reserved-name guard ever drifts.
    functions: list[Tool] = []
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
                functions.append(_build_function_tool(db, agent=agent, tool=t, deps=deps))
            case AgentToolType.LOCAL_SHELL:
                continue
    return out + functions


__all__ = [
    "AgentToolDeps",
    "BuiltinToolDeps",
    "build_agent_tools",
    "default_builtin_deps",
]
