"""Agents facade — public read surface for other contexts.

Conversation (F), Orchestration (G) and Workflow (H) contexts consult
this facade instead of reaching into `contexts.agents.infrastructure`
directly. Keeps the import graph acyclic.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.models import Agent, McpBinding
from contexts.agents.infrastructure.repositories import (
    AgentMcpBindingRepository,
    AgentRepository,
)


class AgentsFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._agents = AgentRepository(db)
        self._bindings = AgentMcpBindingRepository(db)

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

    async def list_mcp_bindings(self, agent_id: uuid.UUID) -> list[McpBinding]:
        return list(await self._bindings.list(agent_id))


__all__ = ["AgentsFacade"]
