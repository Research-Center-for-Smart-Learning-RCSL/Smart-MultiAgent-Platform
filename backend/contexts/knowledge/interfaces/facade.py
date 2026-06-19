"""Knowledge facade — cross-context read surface.

Used by the agents context (to validate `rag_config_id` attach requests)
and conversation / workflow contexts (to resolve a config's `project_id`
for permission filtering).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.graphrag import GraphRagConfig
from contexts.knowledge.domain.models import RagConfig, RagDocument
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.repositories import RagConfigRepository


class KnowledgeFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
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

    async def finalize_rag_upload(
        self,
        *,
        rag_config_id: uuid.UUID,
        filename: str,
        mime: str,
        staging_path: str,
        size_bytes: int,
        uploaded_by: uuid.UUID | None,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> RagDocument:
        """Finalize a completed tus ``purpose=rag_source`` upload (E.6).

        Delegates to :class:`RagTusFinalizer` — streams the staged blob into
        MinIO, creates the ``rag_documents`` row, and enqueues the embed worker.
        """
        from contexts.knowledge.application.rag_tus_finalizer import RagTusFinalizer

        return await RagTusFinalizer(self._db).finalize(
            rag_config_id=rag_config_id,
            filename=filename,
            mime=mime,
            staging_path=staging_path,
            size_bytes=size_bytes,
            uploaded_by=uploaded_by,
            actor_ip=actor_ip,
            request_id=request_id,
        )


__all__ = ["KnowledgeFacade"]
