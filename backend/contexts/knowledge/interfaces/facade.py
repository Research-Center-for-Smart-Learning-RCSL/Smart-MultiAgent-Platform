"""Knowledge facade — cross-context read surface.

Used by the agents context (to validate `rag_config_id` attach requests)
and conversation / workflow contexts (to resolve a config's `project_id`
for permission filtering).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.graphrag import (
    AgentConceptMapCoverage,
    ConceptMapOwnerOption,
    GraphRagConfig,
)
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapDocument
from contexts.knowledge.domain.models import (
    EmbedCatalogEntry,
    RagConfig,
    RagDocument,
    embedding_catalog,
)
from contexts.knowledge.infrastructure.graphrag_repositories import (
    GraphRagConfigRepository,
)
from contexts.knowledge.infrastructure.knowmap_repositories import KnowmapConfigRepository
from contexts.knowledge.infrastructure.repositories import RagConfigRepository
from shared_kernel import audit

if TYPE_CHECKING:
    from contexts.knowledge.application.graphrag_graph_service import GraphView
    from contexts.knowledge.application.graphrag_triggers import GraphRagBuildTrigger
    from contexts.knowledge.application.knowmap_graph_service import KnowmapGraphView

_log = logging.getLogger(__name__)


class KnowledgeFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = RagConfigRepository(db)
        self._graphrag = GraphRagConfigRepository(db)
        self._knowmap = KnowmapConfigRepository(db)

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

    async def list_agent_concept_map_coverage(
        self,
        agent_id: uuid.UUID,
    ) -> Sequence[AgentConceptMapCoverage]:
        """Concept Maps covering an agent for the read-only coverage view (R11.09).

        The api layer authorizes on the agent's project first; this only assembles
        the read model. See
        :meth:`GraphRagConfigRepository.list_coverage_for_agent`.
        """
        return await self._graphrag.list_coverage_for_agent(agent_id)

    async def list_concept_map_owner_options(
        self,
        project_id: uuid.UUID,
    ) -> Sequence[ConceptMapOwnerOption]:
        """Owners in a project without a Concept Map, for the overview create picker.

        The api layer authorizes on the project first; this only assembles the read
        model. See :meth:`GraphRagConfigRepository.list_owner_options`.
        """
        return await self._graphrag.list_owner_options(project_id)

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
        chatroom_id: uuid.UUID,
        agent_ids: Sequence[uuid.UUID],
    ) -> Sequence[GraphRagBuildTrigger]:
        """Return GraphRAG configs whose message triggers fired after a user send.

        F-3: coverage is resolved by the sending room (chatroom / enabled
        agent_group / enabled workspace owners), so the caller passes the
        ``chatroom_id`` alongside the room's bound agents.
        """
        from contexts.knowledge.application.graphrag_triggers import (
            evaluate_graphrag_message_triggers,
        )

        return await evaluate_graphrag_message_triggers(
            self._db, chatroom_id=chatroom_id, agent_ids=agent_ids
        )

    async def get_knowmap_config(
        self, config_id: uuid.UUID, *, include_deleted: bool = False
    ) -> KnowmapConfig | None:
        """Resolve a Knowledge Map config (used by agents to validate attachment).

        Mirrors :meth:`get_rag_config`; the agents context calls it to confirm an
        attached ``knowmap_config_id`` belongs to the agent's project (SEC-H1).
        """
        return await self._knowmap.get(config_id, include_deleted=include_deleted)

    async def get_knowmap_graph(
        self,
        config_id: uuid.UUID,
        *,
        limit: int,
    ) -> KnowmapGraphView:
        """Bounded node/edge view of a Knowledge Map config's Neo4j subgraph.

        Mirrors :meth:`get_graphrag_graph` (Phase 3β, R11.24). The api layer
        authorizes on the config's ``project_id`` first (via
        :meth:`get_knowmap_config`); this only assembles the read model.
        """
        from contexts.knowledge.application.knowmap_graph_service import (
            KnowmapGraphService,
        )

        return await KnowmapGraphService(self._db).get_graph(config_id=config_id, limit=limit)

    async def finalize_knowmap_upload(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        filename: str,
        mime: str,
        staging_path: str,
        size_bytes: int,
        uploaded_by: uuid.UUID | None,
        actor_ip: str | None,
        agent_ids: list[uuid.UUID] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapDocument:
        """Finalize a completed tus ``purpose=knowmap_source`` upload.

        Delegates to :class:`KnowmapTusFinalizer` — streams the staged blob into
        MinIO, creates the ``knowmap_documents`` row, and enqueues the index +
        scan workers. ``agent_ids`` is the per-agent allowlist for a newly created
        document (validated at tus-create); ignored on dedup of an existing doc.
        """
        from contexts.knowledge.application.knowmap_tus_finalizer import KnowmapTusFinalizer

        return await KnowmapTusFinalizer(self._db).finalize(
            knowmap_config_id=knowmap_config_id,
            filename=filename,
            mime=mime,
            staging_path=staging_path,
            size_bytes=size_bytes,
            uploaded_by=uploaded_by,
            actor_ip=actor_ip,
            agent_ids=agent_ids or [],
            request_id=request_id,
        )

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

    async def purge_project_source_infra_batch(
        self,
        project_ids: Iterable[uuid.UUID],
        *,
        audit_action: str = "rag.source_infra_purged",
    ) -> int:
        """Erase source infra for many projects, sharing one Qdrant client (F-24).

        The cross-context entry point both tenant hard-delete paths (the retention
        worker and the immediate admin GDPR purge) call. Keyed on ``project_id``
        alone — no ``rag_documents`` rows required — so it runs after the Postgres
        cascade has erased them. Builds a single Qdrant client for the whole batch
        (rather than one per project), then erases each project's blobs + collection
        and records a per-project audit carrying only ``project_id`` + counts (never
        a blob key or content, §Security). Per-project failures are isolated — one
        raising never aborts the batch. Returns the count purged without error.
        """
        ids = list(project_ids)
        if not ids:
            return 0

        from qdrant_client import AsyncQdrantClient

        from app.config.settings import get_settings
        from contexts.knowledge.infrastructure.qdrant_store import QdrantStore

        settings = get_settings()
        qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
        try:
            return await self._purge_source_infra_with_store(
                ids, store=QdrantStore(qclient), audit_action=audit_action
            )
        finally:
            await qclient.close()

    async def sweep_rag_source_orphans(self, live_project_ids: set[uuid.UUID]) -> int:
        """Discover and erase orphaned source infra with one shared client (F-24).

        The retention backstop. Enumerates the external stores for source blobs /
        ``rag_{project_id}`` collections whose ``project_id`` has no ``projects``
        row (``live_project_ids`` is *every* row, Q-4), then erases each — reusing a
        single Qdrant client across both the enumeration and the teardown. Audits
        each orphan as ``rag.source_orphan_swept``. Returns the count swept.
        """
        from qdrant_client import AsyncQdrantClient

        from app.config.settings import get_settings
        from contexts.knowledge.application.config_service import RagConfigService
        from contexts.knowledge.infrastructure.qdrant_store import QdrantStore

        settings = get_settings()
        qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
        try:
            store = QdrantStore(qclient)
            orphans = await RagConfigService.list_rag_source_orphans(
                live_project_ids=live_project_ids, qdrant_store=store
            )
            return await self._purge_source_infra_with_store(
                orphans, store=store, audit_action="rag.source_orphan_swept"
            )
        finally:
            await qclient.close()

    async def _purge_source_infra_with_store(
        self,
        project_ids: Iterable[uuid.UUID],
        *,
        store: object,
        audit_action: str,
    ) -> int:
        """Erase + audit source infra for each id, reusing ``store`` (F-24).

        Per-project isolation: a project whose teardown raises is logged and
        skipped so it never aborts the batch/sweep. Audit metadata carries only
        ``project_id`` + counts, never a blob key.
        """
        from contexts.knowledge.application.config_service import RagConfigService

        swept = 0
        for pid in project_ids:
            try:
                summary = await RagConfigService.purge_project_source_infra(
                    project_id=pid, qdrant_store=store
                )
            except Exception:
                _log.exception("rag source teardown failed for project %s", pid)
                continue
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action=audit_action,
                    resource_type="project",
                    resource_id=pid,
                    metadata={
                        "project_id": str(pid),
                        "blobs_removed": summary["blobs_removed"],
                        "buckets_failed": summary["buckets_failed"],
                        "collection_dropped": summary["collection_dropped"],
                    },
                ),
            )
            swept += 1
        return swept


__all__ = ["KnowledgeFacade"]
