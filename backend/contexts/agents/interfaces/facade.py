"""Agents facade -- public read surface for other contexts.

Conversation (F), Orchestration (G) and Workflow (H) contexts consult
this facade instead of reaching into `contexts.agents.infrastructure`
directly. Keeps the import graph acyclic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.errors import AgentVersionMismatch
from contexts.agents.domain.models import (
    Agent,
    AgentDraft,
    AgentTool,
    ChatModelCatalogEntry,
    WorkspaceFile,
    chat_model_catalog,
)
from contexts.agents.infrastructure.repositories import (
    AgentRepository,
    AgentToolRepository,
    WorkspaceFileRepository,
)

__all__ = [
    "Agent",
    "AgentDraft",
    "AgentTool",
    "AgentVersionMismatch",
    "AgentsFacade",
    "ChatModelCatalogEntry",
    "EgressResponse",
    "McpInvokeResult",
    "WorkspaceFile",
]


@dataclass(frozen=True, slots=True)
class McpInvokeResult:
    """Neutral result of a headless MCP tool invocation (no agents-domain type
    crosses the facade). ``ok`` is False on a non-zero exit, an egress denial, or
    a timeout — the caller maps that to its own failure state."""

    ok: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class EgressResponse:
    """Neutral result of an outbound egress request through the proxy.

    ``blocked`` is ``None`` on a completed round-trip (``status``/``body`` set);
    otherwise it names why the call was refused (``allowlist`` / ``rate_limit`` /
    ``rate_limiter_offline`` / ``policy``) and ``status`` is ``None``."""

    status: int | None
    body: bytes
    blocked: str | None


class AgentsFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._agents = AgentRepository(db)
        self._tools = AgentToolRepository(db)
        self._workspace_files = WorkspaceFileRepository(db)

    def chat_model_catalog(self) -> tuple[ChatModelCatalogEntry, ...]:
        """Per-provider preset chat models + runtime default for the agent UI."""
        return chat_model_catalog()

    async def get_agent(self, agent_id: uuid.UUID, *, include_deleted: bool = False) -> Agent | None:
        return await self._agents.get(agent_id, include_deleted=include_deleted)

    async def list_agents_for_project(
        self,
        project_id: uuid.UUID,
    ) -> list[Agent]:
        return list(await self._agents.list_for_project(project_id))

    async def list_agents_with_authored_snapshot(self) -> list[Agent]:
        """Active agents with non-null wakeup_authored_snapshot (G.5)."""
        return list(await self._agents.list_with_authored_snapshot())

    async def count_agents_for_key_groups(self, group_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """Active-agent count per Key Group — consumed by the keys context to
        annotate a key's per-project binding footprint."""
        return await self._agents.count_active_by_key_groups(group_ids)

    # ------------------------------------------------------------------
    # Write surface exposed to orchestration (G.4 / G.5)
    # ------------------------------------------------------------------

    async def patch_agent(
        self,
        *,
        agent_id: uuid.UUID,
        draft: AgentDraft,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
    ) -> Agent:
        """Delegate to ``AgentService.patch`` for wakeup-config updates.

        Lazy-imports ``AgentService`` to avoid circular dependency at
        module level (AgentService depends on other contexts' facades).
        """
        from contexts.agents.application.agent_service import AgentService

        svc = AgentService(self._db)
        return await svc.patch(
            agent_id=agent_id,
            draft=draft,
            expected_version=expected_version,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
        )

    async def detach_agents_colliding_with_knowmap_builder(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        new_builder_key_group_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Detach agents whose consumer key group collides with a Knowledge Map's
        new builder key group (R11.25 / F-14).

        The sanctioned seam for the knowledge config service to reconcile attached
        agents on a builder-group change without writing ``agents`` itself — the
        agents context owns the invariant, the table, and the advisory lock.
        Lazy-imports ``AgentService`` to keep the import graph acyclic.
        """
        from contexts.agents.application.agent_service import AgentService

        return await AgentService(self._db).detach_agents_colliding_with_knowmap_builder(
            knowmap_config_id=knowmap_config_id,
            new_builder_key_group_id=new_builder_key_group_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def clear_config_bindings(
        self,
        *,
        project_id: uuid.UUID,
        rag_config_id: uuid.UUID | None = None,
        knowmap_config_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Unbind agents from a RAG / Knowledge Map config being soft-deleted (F-18).

        The sanctioned seam for the knowledge delete services to null the
        per-agent binding the DB ``SET NULL`` FK cannot (a soft-delete is an
        UPDATE, not a DELETE) and to reconcile the dependent File Search tool —
        the agents context owns that table and invariant. Mirrors the
        knowledge → agents write direction of
        :meth:`detach_agents_colliding_with_knowmap_builder`; the knowledge
        context never writes ``agents`` itself. Returns the unbound agent ids so
        the caller can record them in its delete audit. Lazy-imports
        ``AgentService`` to keep the import graph acyclic.
        """
        from contexts.agents.application.agent_service import AgentService

        return await AgentService(self._db).clear_config_bindings(
            project_id=project_id,
            rag_config_id=rag_config_id,
            knowmap_config_id=knowmap_config_id,
        )

    async def restore_agent(
        self,
        *,
        resource_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted agent (R8.13)."""
        from contexts.agents.application.agent_service import AgentService

        return await AgentService(self._db).admin_restore(
            agent_id=resource_id,
            admin_user_id=admin_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def list_agent_tools(self, agent_id: uuid.UUID) -> list[AgentTool]:
        return list(await self._tools.list(agent_id))

    async def list_workspace_files(self, agent_id: uuid.UUID) -> list[WorkspaceFile]:
        return list(await self._workspace_files.list(agent_id))

    # ------------------------------------------------------------------
    # Validator composition seam (activities-platform-core §5.2)
    #
    # The activities validator worker composes MCP/webhook capability THROUGH
    # this facade only — it never imports contexts/agents infrastructure. Both
    # methods source the sandbox runner / egress proxy from the composition-root
    # deps and return neutral value objects.
    # ------------------------------------------------------------------

    async def invoke_mcp_tool(
        self,
        *,
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        tool_name: str,
        arguments: dict[str, Any],
        source: str = "",
        reference: str = "",
        auth: dict[str, Any] | None = None,
        timeout_s: float = 60.0,
    ) -> McpInvokeResult:
        """Run an MCP tool headlessly in the gVisor sandbox (turn-independent).

        An egress denial (sandbox exit 42) or a timeout is surfaced as
        ``ok=False`` rather than raised, so a validator worker degrades to
        ``validation_status=error`` instead of crashing."""
        from contexts.agents.application.runtime.builtin_tools import default_builtin_deps
        from contexts.agents.domain.errors import McpEgressDenied, McpTimeout

        runner = default_builtin_deps().runner
        try:
            res = await runner.invoke_mcp_tool(
                agent_id=agent_id,
                binding_id=binding_id,
                tool_name=tool_name,
                arguments=dict(arguments),
                project_id=project_id,
                source=source,
                reference=reference,
                auth=auth,
                timeout_s=timeout_s,
            )
        except McpEgressDenied:
            return McpInvokeResult(ok=False, stdout="", stderr="egress denied", exit_code=42, duration_ms=0)
        except McpTimeout:
            return McpInvokeResult(ok=False, stdout="", stderr="mcp timeout", exit_code=-1, duration_ms=0)
        return McpInvokeResult(
            ok=res.ok,
            stdout=res.stdout,
            stderr=res.stderr,
            exit_code=res.exit_code,
            duration_ms=res.duration_ms,
        )

    async def egress_request(
        self,
        *,
        project_id: uuid.UUID,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        upstream_auth: tuple[str, str] | None = None,
        timeout_s: float = 30.0,
    ) -> EgressResponse:
        """Make an outbound request through the SSRF-safe egress proxy, sharing
        the same allowlist + rate-limit policy as agent function tools.

        A refusal (allowlist / rate-limit / proxy egress policy) returns a
        ``blocked`` :class:`EgressResponse` rather than raising, so the caller
        classifies it as a validation ``error``."""
        from contexts.agents.application.egress import (
            EgressBlocked,
            perform_egress_request,
        )
        from contexts.agents.application.runtime.builtin_tools import default_builtin_deps
        from contexts.agents.domain.errors import McpEgressDenied

        proxy = default_builtin_deps().proxy
        try:
            outcome = await perform_egress_request(
                self._db,
                project_id=project_id,
                method=method,
                url=url,
                proxy=proxy,
                args=body,
                headers=headers,
                upstream_auth=upstream_auth,
                timeout_s=timeout_s,
                rate_limit_prefix="validator",
            )
        except EgressBlocked as blocked:
            return EgressResponse(status=None, body=b"", blocked=blocked.kind)
        except McpEgressDenied:
            return EgressResponse(status=None, body=b"", blocked="policy")
        return EgressResponse(status=outcome.status, body=outcome.body, blocked=None)
