"""Agent-groups facade — public surface for the web layer (Phase 2b WS2)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.application.group_service import AgentGroupService


class AgentGroupFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._service = AgentGroupService(db)

    async def create_group(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return await self._service.create_group(
            project_id=project_id,
            name=name,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def add_member(
        self,
        *,
        group_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._service.add_member(
            group_id=group_id,
            agent_id=agent_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def remove_member(
        self,
        *,
        group_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        return await self._service.remove_member(
            group_id=group_id,
            agent_id=agent_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def list_members(self, group_id: uuid.UUID) -> Sequence[uuid.UUID]:
        return await self._service.list_members(group_id)

    async def group_project_id(self, group_id: uuid.UUID) -> uuid.UUID | None:
        return await self._service.group_project_id(group_id)


__all__ = ["AgentGroupFacade"]
