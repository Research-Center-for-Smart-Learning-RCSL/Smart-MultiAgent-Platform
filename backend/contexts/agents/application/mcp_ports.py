"""Protocols for the MCP sandbox + egress + built-in tool subsystems.

These protocols keep the application layer framework-free: concrete Docker,
httpx, and Redis implementations live in ``infrastructure/``. Tests satisfy
the protocols with plain fakes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from contexts.agents.domain.mcp import McpTestResult, SearchResult, StagedFile, ToolCallResult


class SandboxRunner(Protocol):
    """Run MCP servers / built-in tools inside a gVisor-isolated container.

    Responsibilities:

    - ``probe(binding)`` — run ``initialize`` + ``tools/list`` against an MCP
      server; used by the Test endpoint.
    - ``invoke_mcp_tool(...)`` — used at chat turn time.
    - ``run_file_op(...)`` / ``run_code_exec(...)`` — built-in tools run in
      their own sandbox images (``file`` uses the per-agent named volume;
      ``code_exec`` uses the curated ``python:3.12-slim`` image).
    - ``stage_*(...)`` — write files into the agent's persistent volume so
      ``code_exec`` can read them.
    """

    async def probe(
        self,
        *,
        agent_id: uuid.UUID,
        source: str,
        reference: str,
        allowed_tools: Sequence[str],
        auth: dict[str, Any] | None,
        project_id: uuid.UUID,
        timeout_s: float = 20.0,
    ) -> McpTestResult: ...

    async def invoke_mcp_tool(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        tool_name: str,
        arguments: dict[str, Any],
        project_id: uuid.UUID,
        source: str = "",
        reference: str = "",
        auth: dict[str, Any] | None = None,
        timeout_s: float = 60.0,
    ) -> ToolCallResult: ...

    async def run_file_op(
        self,
        *,
        agent_id: uuid.UUID,
        op: Literal["list", "read", "write"],
        path: str,
        data: bytes | None = None,
        timeout_s: float = 10.0,
    ) -> ToolCallResult: ...

    async def run_code_exec(
        self,
        *,
        agent_id: uuid.UUID,
        source: str,
        stdin: str = "",
        timeout_s: float = 30.0,
        chatroom_id: uuid.UUID | None = None,
    ) -> ToolCallResult:
        """Run a Python snippet in the gVisor sandbox.

        When ``chatroom_id`` is given the call is routed to a persistent,
        session-scoped kernel that keeps in-memory state across calls (the
        Code-Interpreter path); otherwise it runs run-and-burn.
        """
        ...

    # -- staging ------------------------------------------------------------
    #
    # The three writers into the agent's persistent volume, each owning a disjoint
    # subtree of ``/workspace``. All three return **absolute** paths, because the note
    # the model reads is shared and the kernel's cwd is the session dir while the
    # ``file`` tool roots relative paths at the volume root — only absolute means the
    # same file to both.
    #
    # Declared here rather than left duck-typed: the turn engine calls them through an
    # untyped ``runner``, so before this nothing type-checked the call and a signature
    # drift between caller and implementation was invisible to mypy.

    async def stage_kernel_inputs(
        self,
        *,
        agent_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        files: Sequence[StagedFile],
    ) -> list[str]:
        """Copy a message's uploads into ``/workspace/sessions/{room}/inputs/``."""
        ...

    async def stage_agent_workspace_files(
        self,
        *,
        agent_id: uuid.UUID,
        files: Sequence[StagedFile],
        manifest_sha: str,
    ) -> list[str]:
        """Materialise the agent's persisted files under ``/workspace/agent-files/``.

        Idempotent on ``manifest_sha``; a repeat call with an unchanged sha writes nothing.
        """
        ...

    async def stage_skill_files(
        self,
        *,
        agent_id: uuid.UUID,
        files: Sequence[StagedFile],
        manifest_sha: str,
    ) -> list[str]:
        """Materialise bound skills' scripts under ``/workspace/skills/`` ([R31.22]).

        Each filename must be ``{skill_name}/{path_within_skill}``. Idempotent on
        ``manifest_sha``, against a cache of its own so the two file sets above and this
        one never evict each other.
        """
        ...


class EgressProxyClient(Protocol):
    """Caller-side surface of the Egress Proxy.

    Implementations attach the shared HMAC header so the proxy trusts the
    project id. Any request whose hostname is not on the project's allowlist
    raises ``McpEgressDenied``.
    """

    async def request(
        self,
        *,
        method: str,
        url: str,
        project_id: uuid.UUID,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        timeout_s: float = 20.0,
        upstream_auth: tuple[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]: ...


class SearchAdapter(Protocol):
    """§12.4 — BYO search adapter contract."""

    name: str

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        locale: str,
        freshness: Literal["any", "day", "week", "month", "year"],
        api_key: bytes,
        proxy: EgressProxyClient,
        project_id: uuid.UUID,
        config: dict[str, Any] | None = None,
    ) -> list[SearchResult]: ...


class SearchCache(Protocol):
    async def get(self, cache_key: str) -> list[SearchResult] | None: ...
    async def set(self, cache_key: str, results: list[SearchResult], *, ttl_s: int) -> None: ...


class SearchRateLimiter(Protocol):
    async def try_acquire(self, *, project_id: uuid.UUID, limit_per_minute: int) -> bool: ...


__all__ = [
    "EgressProxyClient",
    "SandboxRunner",
    "SearchAdapter",
    "SearchCache",
    "SearchRateLimiter",
]
