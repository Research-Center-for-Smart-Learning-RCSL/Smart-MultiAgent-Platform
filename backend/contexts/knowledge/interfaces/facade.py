"""Knowledge facade — cross-context read surface.

Used by the agents context (to validate `rag_config_id` attach requests)
and conversation / workflow contexts (to resolve a config's `project_id`
for permission filtering).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.models import RagConfig
from contexts.knowledge.infrastructure.repositories import RagConfigRepository


class KnowledgeFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._configs = RagConfigRepository(db)

    async def get_rag_config(
        self, config_id: uuid.UUID, *, include_deleted: bool = False
    ) -> RagConfig | None:
        return await self._configs.get(config_id, include_deleted=include_deleted)


__all__ = ["KnowledgeFacade"]
