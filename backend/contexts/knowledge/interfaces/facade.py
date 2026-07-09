"""Knowledge facade — cross-context read surface.

Used by the agents context (to validate `rag_config_id` attach requests)
and conversation / workflow contexts (to resolve a config's `project_id`
for permission filtering).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.graphrag import GraphRagConfig
from contexts.knowledge.domain.models import (
    EmbedCatalogEntry,
    RagConfig,
    RagDocument,
    embedding_catalog,
)
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.repositories import RagConfigRepository

if TYPE_CHECKING:
    from contexts.knowledge.application.graphrag_graph_service import GraphView
    from contexts.knowledge.application.graphrag_triggers import GraphRagBuildTrigger


class KnowledgeFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = RagConfigRepository(db)
        self._graphrag = GraphRagConfigRepository(db)

    def embedding_catalog(self) -> tuple[EmbedCatalogEntry, ...]:
        """Per-provider whitelisted embedding models + dimensions for the RAG UI."""
        return embedding_catalog()

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

    async def list_graph_configs_for_agent(
        self,
        agent_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        """Live GraphRAG configs owned by an agent (agent-delete cascade).

        Used by ``delete_agent`` to enumerate the configs whose external
        stores must be purged when the owning agent is removed.
        """
        return await self._graphrag.list_for_agents([agent_id])

    async def list_graph_configs_for_owner(
        self,
        *,
        owner_kind: str,
        owner_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        """Live GraphRAG configs owned by a chatroom / workspace / agent_group.

        Used by the owner-delete cascade (WS6) to enumerate the configs whose
        external stores must be purged when the owning resource is deleted.
        """
        return await self._graphrag.list_for_owner(owner_kind=owner_kind, owner_id=owner_id)

    async def resolve_graphrag_layers(
        self,
        *,
        agent_id: uuid.UUID,
        chatroom_id: uuid.UUID,
    ) -> Sequence[GraphRagConfig]:
        """Covering Concept Map layers for an agent's turn, narrow -> wide (WS4).

        The chatroom map, each enabled agent_group map the agent is a live member
        of, and the enabled workspace map — ordered for the tiered retrieval
        assembler. Disabled wide layers and non-member groups are excluded.
        """
        return await self._graphrag.list_layers_for_turn(agent_id=agent_id, chatroom_id=chatroom_id)

    async def soft_delete_graph_config(
        self,
        config_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Soft-delete a GraphRAG config via the service (does not commit).

        Routes through :class:`GraphRagConfigService` so the agent-delete
        cascade emits the canonical ``graphrag.deleted`` audit event (and any
        future service-level side effects) exactly like a direct config delete,
        rather than silently soft-deleting the row at the repository. The caller
        owns the transaction and must commit before purging external stores
        (DOM-4).
        """
        from contexts.knowledge.application.graphrag_config_service import (
            GraphRagConfigService,
        )

        await GraphRagConfigService(self._db).soft_delete(
            config_id=config_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    @staticmethod
    async def purge_graph_config_external_stores(
        *,
        config_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict[str, bool]:
        """Best-effort Neo4j + Qdrant purge for a deleted config (DOM-4).

        Must run only after the soft-delete + audit row are committed.
        Returns a summary dict for the follow-up audit row.
        """
        from contexts.knowledge.application.graphrag_config_service import (
            GraphRagConfigService,
        )

        return await GraphRagConfigService.cascade_external_stores(
            config_id=config_id,
            project_id=project_id,
        )

    async def get_graphrag_graph(
        self,
        config_id: uuid.UUID,
        *,
        limit: int,
    ) -> GraphView:
        """Bounded node/edge view of a config's Neo4j subgraph (viz P0).

        The api layer authorizes on the config's ``project_id`` first (via
        :meth:`get_graphrag_config`); this only assembles the read model.
        """
        from contexts.knowledge.application.graphrag_graph_service import (
            GraphRagGraphService,
        )

        return await GraphRagGraphService(self._db).get_graph(config_id=config_id, limit=limit)

    async def evaluate_graphrag_message_triggers(
        self,
        *,
        agent_ids: Sequence[uuid.UUID],
    ) -> Sequence[GraphRagBuildTrigger]:
        """Return GraphRAG configs whose message triggers fired after a user send."""
        from contexts.knowledge.application.graphrag_triggers import (
            evaluate_graphrag_message_triggers,
        )

        return await evaluate_graphrag_message_triggers(self._db, agent_ids=agent_ids)

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
        agent_ids: list[uuid.UUID] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> RagDocument:
        """Finalize a completed tus ``purpose=rag_source`` upload (E.6).

        Delegates to :class:`RagTusFinalizer` — streams the staged blob into
        MinIO, creates the ``rag_documents`` row, and enqueues the embed worker.
        ``agent_ids`` is the per-agent allowlist for a newly created document
        (validated at the tus-create boundary); ignored on dedup of an existing
        document so a re-upload never silently rewrites another upload's scope.
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
            agent_ids=agent_ids or [],
            request_id=request_id,
        )


__all__ = ["KnowledgeFacade"]
