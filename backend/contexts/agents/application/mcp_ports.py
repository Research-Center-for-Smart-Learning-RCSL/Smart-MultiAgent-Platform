"""Protocols for the MCP sandbox + egress + built-in tool subsystems.

These protocols keep the application layer framework-free: concrete Docker,
httpx, and Redis implementations live in ``infrastructure/``. Tests satisfy
the protocols with plain fakes.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from contexts.agents.domain.mcp import McpTestResult, SearchResult, ToolCallResult


class SandboxRunner(Protocol):
    """Run MCP servers / built-in tools inside a gVisor-isolated container.

    Two responsibilities:

    - ``probe(binding)`` — run ``initialize`` + ``tools/list`` against an MCP
      server; used by the Test endpoint.
    - ``invoke_mcp_tool(...)`` — used at chat turn time.
    - ``run_file_op(...)`` / ``run_code_exec(...)`` — built-in tools run in
      their own sandbox images (``file`` uses the per-agent named volume;
      ``code_exec`` uses the curated ``python:3.12-slim`` image).
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
    ) -> ToolCallResult: ...


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
