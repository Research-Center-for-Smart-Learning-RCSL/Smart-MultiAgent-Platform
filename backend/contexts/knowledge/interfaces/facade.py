"""Knowledge facade — cross-context read surface.

Used by the agents context (to validate `rag_config_id` attach requests)
and conversation / workflow contexts (to resolve a config's `project_id`
for permission filtering).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.graphrag import GraphRagConfig
from contexts.knowledge.domain.models import RagConfig
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.repositories import RagConfigRepository


class KnowledgeFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._configs = RagConfigRepository(db)
        self._graphrag = GraphRagConfigRepository(db)

    async def get_rag_config(
        self, config_id: uuid.UUID, *, include_deleted: bool = False
    ) -> RagConfig | None:
        return await self._configs.get(config_id, include_deleted=include_deleted)

    async def get_graphrag_config(
        self, config_id: uuid.UUID, *, include_deleted: bool = False
    ) -> GraphRagConfig | None:
        """Resolve a GraphRAG config (used by agents to validate attachment).

        Mirrors :meth:`get_rag_config`; the agents context calls both to
        confirm an attached config belongs to the agent's project (SEC-H1).
        """
        return await self._graphrag.get(config_id, include_deleted=include_deleted)


__all__ = ["KnowledgeFacade"]
