"""Agents facade -- public read surface for other contexts.

Conversation (F), Orchestration (G) and Workflow (H) contexts consult
this facade instead of reaching into `contexts.agents.infrastructure`
directly. Keeps the import graph acyclic.
"""

from __future__ import annotations

import uuid

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
    "WorkspaceFile",
]


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

    async def list_agent_tools(self, agent_id: uuid.UUID) -> list[AgentTool]:
        return list(await self._tools.list(agent_id))

    async def list_workspace_files(self, agent_id: uuid.UUID) -> list[WorkspaceFile]:
        return list(await self._workspace_files.list(agent_id))
