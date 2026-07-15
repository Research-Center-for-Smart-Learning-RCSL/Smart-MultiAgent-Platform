"""Knowledge Map config CRUD use-cases (Phase 3, R11.12/R11.13).

Mirrors :class:`RagConfigService` + :class:`GraphRagConfigService`: the config
owns validation (builder key group belongs to the project; a resolvable embedding
key exists; the project embedding dimension is consistent), audit emission,
soft-delete with child-document cascade, and the pinned-key embedder construction
shared by the ingest + build paths.

Unlike a Concept Map it has no discriminated owner and no temporality; unlike
file-RAG it resolves its embedding key from a builder Key Group (SEC-H3 carried
path) rather than a single ``embed_key_id`` column.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.interfaces.facade import KeysFacade
from contexts.knowledge.application.embed_resolution import (
    resolve_embed_key,
    resolve_pinned_embed_key,
)
from contexts.knowledge.domain.embedding_pin import PinKind
from contexts.knowledge.domain.errors import (
    KnowmapBuilderKeyGroupProjectMismatch,
    KnowmapConfigNotFound,
    KnowmapEmbedDimensionConflict,
    KnowmapEmbeddingModelChangeBlocked,
    KnowmapNoEmbeddingKey,
)
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapConfigDraft, KnowmapDocument
from contexts.knowledge.domain.models import embed_dimension
from contexts.knowledge.infrastructure.embedding_pin_repository import EmbeddingPinRepository
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.db.advisory_lock import advisory_xact_lock, knowmap_builder_lock_key

_log = logging.getLogger(__name__)


class KnowmapConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = KnowmapConfigRepository(db)
        self._pins = EmbeddingPinRepository(db)

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: KnowmapConfigDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapConfig:
        await self._assert_builder_group_in_project(draft.builder_key_group_id, project_id)
        # The builder group MUST resolve an embedding key: the corpus is chunked
        # and graph entities are embedded into knowmap_{project_id} at build, so a
        # config that can never embed is invalid (unlike graphrag's nullable pin).
        pin = await self._resolve_group_pin(draft.builder_key_group_id)
        if pin is None:
            raise KnowmapNoEmbeddingKey(
                f"builder key group {draft.builder_key_group_id} has no actively-carried embedding key"
            )
        embed_provider, embed_model, embed_dim = pin
        existing_dim = await self._configs.project_pinned_dim(project_id)
        if existing_dim is not None and existing_dim != embed_dim:
            raise KnowmapEmbedDimensionConflict(
                f"project {project_id} is pinned to {existing_dim}-dim knowmap embeddings; "
                f"builder key group {draft.builder_key_group_id} resolves to {embed_dim}-dim"
            )

        # Durable, race-free project dimension pin (F-11). The scan above is a
        # cheap pre-check; this is authoritative and serializes the read-then-
        # insert under an advisory lock, and its pin survives deleting the config
        # that first sized knowmap_{project_id}.
        await self._pins.ensure(
            project_id=project_id,
            kind=PinKind.KNOWMAP,
            provider=embed_provider,
            model=embed_model,
            dim=embed_dim,
            on_conflict=lambda existing, this_dim: KnowmapEmbedDimensionConflict(
                f"project {project_id} is pinned to {existing}-dim knowmap embeddings; "
                f"builder key group {draft.builder_key_group_id} resolves to {this_dim}-dim"
            ),
        )

        cfg = await self._configs.create(
            project_id=project_id,
            name=draft.name,
            builder_key_group_id=draft.builder_key_group_id,
            chunk_strategy=draft.chunk_strategy,
            chunk_params=draft.chunk_params,
            embed_provider=embed_provider,
            embed_model=embed_model,
            embed_dim=embed_dim,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="knowmap.config_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="knowmap_config",
                resource_id=cfg.id,
                metadata={
                    "project_id": str(project_id),
                    "name": cfg.name,
                    "chunk_strategy": cfg.chunk_strategy.value,
                    "builder_key_group_id": str(draft.builder_key_group_id),
                    "embed_provider": embed_provider,
                    "embed_model": embed_model,
                },
                request_id=request_id,
            ),
        )
        return cfg

    async def get(self, config_id: uuid.UUID) -> KnowmapConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[KnowmapConfig]:
        return await self._configs.list_for_project(project_id)

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        patch: dict[str, Any],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> tuple[KnowmapConfig, list[uuid.UUID]]:
        """Update a Knowledge Map config; returns ``(config, detached_agent_ids)``.

        On a builder-key-group change that would collide with attached agents
        (their consumer key group equals the new builder group), each colliding
        agent is automatically detached in this transaction to restore the R11.25
        isolation invariant (F-14); the detached ids are returned so the caller
        can inform the designer. Empty on any non-colliding change.
        """
        cfg = await self.get(config_id)
        new_group = patch.get("builder_key_group_id")
        db_values: dict[str, Any] = {}
        detached_agent_ids: list[uuid.UUID] = []
        for k in ("name", "chunk_params"):
            if k in patch:
                db_values[k] = patch[k]
        if new_group is not None and new_group != cfg.builder_key_group_id:
            # Serialise the builder-group change against concurrent agent
            # attach/re-key on this map (R11.25 / F-14). Held through this
            # transaction's commit so the detach below and the config write are
            # observed atomically by any attach that loses the race.
            await advisory_xact_lock(self._db, knowmap_builder_lock_key(config_id))
            await self._assert_builder_group_in_project(new_group, cfg.project_id)
            pin = await self._resolve_group_pin(new_group)
            if pin is None:
                raise KnowmapNoEmbeddingKey(
                    f"builder key group {new_group} has no actively-carried embedding key"
                )
            provider, model, dim = pin
            existing_dim = await self._configs.project_pinned_dim(cfg.project_id, exclude_config_id=config_id)
            if existing_dim is not None and existing_dim != dim:
                raise KnowmapEmbedDimensionConflict(
                    f"project {cfg.project_id} is pinned to {existing_dim}-dim knowmap embeddings; "
                    f"builder key group {new_group} resolves to {dim}-dim"
                )
            # F-13 (R11.19): reject a swap to a different embedding provider/model
            # while the config holds indexed vectors (fail-closed on any prior
            # build) — old-model vectors persist in knowmap_{project_id} and a
            # query embedded with the new model against them silently collapses
            # recall. The dimension conflict above keeps precedence. A never-built
            # config may change freely. Guard on the resolved (provider, model), so
            # a group change keeping the same embedding stays allowed.
            if (provider, model) != (cfg.embed_provider, cfg.embed_model) and cfg.last_build_at is not None:
                raise KnowmapEmbeddingModelChangeBlocked(
                    f"config {config_id} has indexed vectors under "
                    f"{cfg.embed_provider}:{cfg.embed_model}; changing the embedding model to "
                    f"{provider}:{model} would invalidate them — clear and recreate to change "
                    f"the embedding model"
                )
            db_values["builder_key_group_id"] = new_group
            db_values["embed_provider"] = provider
            db_values["embed_model"] = model
            db_values["embed_dim"] = dim
            # F-14 (R11.25): the new builder group may equal the consumer key
            # group of already-attached agents, silently collapsing the enforced
            # billing/rate-limit split. Reached only after the F-13 model-swap
            # guard above — a rejected update never detaches. Detach each colliding
            # agent (the agents context owns that write) in this same transaction;
            # report the ids so the designer is informed. Uses a local import: the
            # agents context imports KnowledgeFacade, so a module-level AgentsFacade
            # import here would cycle.
            from contexts.agents.interfaces.facade import AgentsFacade

            detached_agent_ids = await AgentsFacade(self._db).detach_agents_colliding_with_knowmap_builder(
                knowmap_config_id=config_id,
                new_builder_key_group_id=new_group,
                project_id=cfg.project_id,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        updated = await self._configs.update(config_id, db_values)
        if updated is None:
            raise KnowmapConfigNotFound(str(config_id))
        if "embed_dim" in db_values:
            # Keep the durable F-11 pin consistent with the config's new embedding
            # (reached only on an allowed change — a blocked model swap raised
            # above). The dimension guard ensured no live sibling pins a different
            # dimension, so overwriting is safe.
            await self._pins.upsert(
                project_id=cfg.project_id,
                kind=PinKind.KNOWMAP,
                provider=db_values["embed_provider"],
                model=db_values["embed_model"],
                dim=db_values["embed_dim"],
            )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="knowmap.config_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="knowmap_config",
                resource_id=config_id,
                metadata={
                    "project_id": str(cfg.project_id),
                    "changed_fields": list(db_values.keys()),
                    "detached_agent_ids": [str(a) for a in detached_agent_ids],
                },
                request_id=request_id,
            ),
        )
        return updated, detached_agent_ids

    async def soft_delete(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Sequence[KnowmapDocument]:
        """Soft-delete the config and hard-delete its child documents.

        Returns the deleted documents (carrying ``minio_path``) so the caller can
        purge Qdrant points + MinIO blobs after the DB commit — infra cleanup must
        trail the durable audit row (DOM-4). ``knowmap_chunks`` cascade via FK.
        """
        cfg = await self.get(config_id)
        docs_repo = KnowmapDocumentRepository(self._db)
        page_size = 10_000
        docs: list[KnowmapDocument] = []
        while True:
            batch = list(await docs_repo.list_for_config(config_id, limit=page_size))
            if not batch:
                break
            docs.extend(batch)
            for doc in batch:
                await docs_repo.delete(doc.id)
            if len(batch) < page_size:
                break
        # F-18: the DB ``ON DELETE SET NULL`` FK (migration 0048) unbinds attached
        # agents only on a physical row DELETE — this soft-delete is an UPDATE, so
        # the binding must be nulled explicitly, through the agents facade in this
        # same transaction. ``knowmap_config_id`` has no dependent tool (there is
        # no Knowledge Map tool), so no tool reconciliation is needed. Local import
        # avoids the agents↔knowledge module-level cycle (see ``update`` above).
        from contexts.agents.interfaces.facade import AgentsFacade

        unbound_agents = await AgentsFacade(self._db).clear_config_bindings(
            project_id=cfg.project_id,
            knowmap_config_id=config_id,
        )
        await self._configs.soft_delete(config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="knowmap.config_deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="knowmap_config",
                resource_id=config_id,
                metadata={
                    "project_id": str(cfg.project_id),
                    "cascaded_documents": len(docs),
                    "unbound_agents": [str(a) for a in unbound_agents],
                },
                request_id=request_id,
            ),
        )
        return docs

    # ---- helpers ----------------------------------------------------------

    async def _assert_builder_group_in_project(
        self, builder_key_group_id: uuid.UUID, project_id: uuid.UUID
    ) -> None:
        # Cross-context check via the keys facade (a project-scoped active group),
        # not a direct query into another context's tables.
        group = await KeysFacade(self._db).get_key_group(builder_key_group_id)
        if group is None or group.project_id != project_id:
            raise KnowmapBuilderKeyGroupProjectMismatch(
                f"builder_key_group_id {builder_key_group_id} does not belong to project {project_id}"
            )

    async def _resolve_group_pin(self, builder_key_group_id: uuid.UUID) -> tuple[str, str, int] | None:
        resolved = await resolve_embed_key(self._db, builder_key_group_id)
        if resolved is None:
            return None
        provider, model, _key_id = resolved
        return provider, model, embed_dimension(provider, model)

    @staticmethod
    def build_ingest_service(db: AsyncSession, *, embedder: Any) -> Any:
        """Construct a :class:`KnowmapIngestService` wired to real MinIO.

        No Qdrant client: knowmap ingest never upserts chunk vectors (chunks are
        the build corpus). The caller owns no external client to close.
        """
        from minio import Minio

        from app.config.settings import get_settings
        from contexts.knowledge.application.knowmap_ingest_service import KnowmapIngestService
        from contexts.knowledge.infrastructure.blob_store import MinioBlobStore

        settings = get_settings()
        minio = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.root_access_key,
            secret_key=settings.minio.root_secret_key,
            secure=settings.minio.use_tls,
            region=settings.minio.region,
        )
        return KnowmapIngestService(
            db,
            blob=MinioBlobStore(minio),
            embedder=embedder,
            bucket=settings.minio.bucket_knowmap_sources,
        )

    async def clear_pin_if_last_config(self, *, project_id: uuid.UUID) -> bool:
        """Clear the durable Knowledge Map pin iff no live config remains (F-11).

        Called in the delete's OWN transaction, before its commit, so the pin clear
        commits atomically with the soft-delete. Under the ``(project, knowmap)``
        advisory lock — held from here through that commit — a concurrent create
        blocks and never observes a config-deleted-but-pin-present state, closing
        the window where it would read the stale pin and be spuriously rejected at a
        different dimension (F-11 W1). Returns ``True`` when the pin was cleared, so
        ``knowmap_{project_id}`` is an orphan to drop after the commit.
        """
        await self._pins.acquire_lock(project_id, PinKind.KNOWMAP)
        if list(await self._configs.list_for_project(project_id)):
            return False
        await self._pins.clear(project_id=project_id, kind=PinKind.KNOWMAP)
        return True

    async def drop_orphan_collection(self, *, project_id: uuid.UUID) -> bool:
        """Drop ``knowmap_{project_id}`` after the delete commit, iff still configless (F-11).

        Runs post-commit (DOM-4: irreversible external deletes trail the durable DB
        commit — W5). Re-acquires the ``(project, knowmap)`` lock and re-checks: if a
        concurrent create re-pinned the project between the two commits, its
        collection is left intact. Best-effort on the Qdrant side. Returns ``True``
        when it dropped.
        """
        await self._pins.acquire_lock(project_id, PinKind.KNOWMAP)
        if list(await self._configs.list_for_project(project_id)):
            return False

        from qdrant_client import AsyncQdrantClient

        from app.config.settings import get_settings
        from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore

        settings = get_settings()
        try:
            qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
            try:
                await GraphRagVectorStore(qclient, prefix="knowmap").delete_collection(project_id)
            finally:
                await qclient.close()
        except Exception:
            _log.exception("knowmap drop-empty: qdrant collection drop failed for project %s", project_id)
        return True

    # ---- infrastructure cascade (WS4, R11.20) -----------------------------

    @staticmethod
    async def purge_document_blobs(
        *,
        docs: Sequence[KnowmapDocument],
    ) -> dict[str, Any]:
        """Best-effort MinIO blob removal for deleted documents (DOM-4).

        Knowmap chunks carry no Qdrant points, so there is no per-document vector
        purge here — the config-level :meth:`cascade_external_stores` tears down
        the graph vectors, and a document delete triggers a rebuild that drops the
        removed document's triples. Must run only after the DB commit.
        """
        summary: dict[str, Any] = {"documents": len(docs), "blobs_removed": 0, "blobs_failed": 0}
        if not docs:
            return summary
        from minio import Minio

        from app.config.settings import get_settings

        settings = get_settings()
        minio_client = None
        try:
            minio_client = Minio(
                settings.minio.endpoint,
                access_key=settings.minio.root_access_key,
                secret_key=settings.minio.root_secret_key,
                secure=settings.minio.use_tls,
                region=settings.minio.region,
            )
        except Exception:
            _log.exception("knowmap infra purge: minio client init failed")
        for d in docs:
            if minio_client is None:
                summary["blobs_failed"] += 1
                continue
            try:
                bucket, _, key = d.minio_path.partition("/")
                if bucket and key:
                    await asyncio.to_thread(minio_client.remove_object, bucket, key)
                    summary["blobs_removed"] += 1
                else:
                    summary["blobs_failed"] += 1
            except Exception:
                summary["blobs_failed"] += 1
                _log.exception("knowmap infra purge: minio remove failed for doc %s", d.id)
        return summary

    @staticmethod
    async def cascade_external_stores(
        *,
        config_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict[str, bool]:
        """Best-effort teardown of a config's Neo4j subgraph + knowmap Qdrant points.

        Mirrors :func:`graphrag_config_service.purge_config_external_stores` with
        the ``knowmap`` collection prefix. Builds short-lived clients (the request
        path owns none) and must run only after the soft-delete + audit commit.
        """
        from qdrant_client import AsyncQdrantClient

        from app.config.settings import get_settings
        from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
        from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver

        settings = get_settings()
        neo4j_conf = getattr(settings, "neo4j", None)
        driver = (
            Neo4jAsyncDriver(uri=neo4j_conf.url, auth=(neo4j_conf.user, neo4j_conf.password))
            if neo4j_conf is not None
            else None
        )
        qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
        neo4j_purged = False
        qdrant_purged = False
        try:
            if driver is not None:
                try:
                    await driver.delete_all(config_id=config_id)
                    neo4j_purged = True
                except Exception:
                    _log.exception("knowmap delete: neo4j cascade failed for config %s", config_id)
            try:
                await GraphRagVectorStore(qclient, prefix="knowmap").delete_by_config(
                    project_id=project_id, config_id=config_id
                )
                qdrant_purged = True
            except Exception:
                _log.exception("knowmap delete: qdrant cascade failed for config %s", config_id)
        finally:
            if driver is not None:
                await driver.close()
            await qclient.close()
        return {"neo4j_purged": neo4j_purged, "qdrant_purged": qdrant_purged}


async def build_knowmap_embedder(db: AsyncSession, cfg: KnowmapConfig) -> Any:
    """Construct the pinned-key embedder for a Knowledge Map's ingest + build.

    Resolves ``(provider, model, key_id)`` honouring the config's project pin
    (Phase 2a D2) through the shared carried-key path, then builds a router-backed
    embedder. The BYO key is unwrapped/scrubbed/accounted inside the router; no
    plaintext enters the caller.
    """
    from contexts.keys.infrastructure.adapters import build_router
    from contexts.knowledge.infrastructure.embedders import router_embedder_for

    provider, model, key_id = await resolve_pinned_embed_key(db, cfg)
    return router_embedder_for(
        router=build_router(db),
        key_id=key_id,
        project_id=cfg.project_id,
        provider=provider,
        model=model,
    )


__all__ = ["KnowmapConfigService", "build_knowmap_embedder"]
