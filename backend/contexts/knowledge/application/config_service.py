"""RAG config use-cases — CRUD with capability validation (R10.05)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.interfaces.facade import (
    ApiKeyProvider,
    CapabilityRequirement,
    KeysCapabilityMismatch,
    KeysFacade,
    ProviderCapability,
    assert_capability,
)
from contexts.knowledge.domain.embedding_pin import PinKind
from contexts.knowledge.domain.errors import (
    CapabilityMismatch,
    EmbedDimensionConflict,
    EmbedModelNotWhitelisted,
    RagConfigNotFound,
)
from contexts.knowledge.domain.models import (
    EMBED_MODEL_WHITELIST,
    RagConfig,
    RagConfigDraft,
    RagDocument,
    embed_dimension,
)
from contexts.knowledge.infrastructure.embedding_pin_repository import EmbeddingPinRepository
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit

_log = logging.getLogger(__name__)

_EMBED = CapabilityRequirement(capability=ProviderCapability.EMBEDDING)
_RERANK = CapabilityRequirement(capability=ProviderCapability.RERANK)


class RagConfigService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = RagConfigRepository(db)
        self._pins = EmbeddingPinRepository(db)
        self._keys_facade = KeysFacade(db)

    async def _validate_embed_key(self, *, key_id: uuid.UUID, provider: str, project_id: uuid.UUID) -> None:
        key = await self._keys_facade.get_key(key_id)
        if key is None:
            raise CapabilityMismatch(f"embed_key {key_id} missing or deleted")
        if not await self._keys_facade.is_key_in_project_scope(key_id, project_id):
            raise CapabilityMismatch(f"embed_key {key_id} does not belong to project")
        if key.provider.value != provider:
            raise CapabilityMismatch(
                f"embed_key provider {key.provider.value!r} != config provider {provider!r}"
            )
        try:
            assert_capability(ApiKeyProvider(key.provider.value), _EMBED)
        except KeysCapabilityMismatch as exc:
            raise CapabilityMismatch(str(exc)) from exc

    async def _validate_rerank_key(self, *, key_id: uuid.UUID, provider: str, project_id: uuid.UUID) -> None:
        key = await self._keys_facade.get_key(key_id)
        if key is None:
            raise CapabilityMismatch(f"rerank_key {key_id} missing or deleted")
        if not await self._keys_facade.is_key_in_project_scope(key_id, project_id):
            raise CapabilityMismatch(f"rerank_key {key_id} does not belong to project")
        if key.provider.value != provider:
            raise CapabilityMismatch(f"rerank_key provider {key.provider.value!r} != {provider!r}")
        try:
            assert_capability(ApiKeyProvider(key.provider.value), _RERANK)
        except KeysCapabilityMismatch as exc:
            raise CapabilityMismatch(str(exc)) from exc

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: RagConfigDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> RagConfig:
        # R10.05 whitelist.
        if (draft.embed_provider, draft.embed_model) not in EMBED_MODEL_WHITELIST:
            raise EmbedModelNotWhitelisted(f"{draft.embed_provider}:{draft.embed_model} not whitelisted")

        # §21.4: the project shares one Qdrant collection sized to the first
        # config's embedding dimension, so a sibling config on a different-
        # dimension model would fail every upsert. Reject the mismatch up front.
        new_dim = embed_dimension(draft.embed_provider, draft.embed_model)
        for existing in await self._configs.list_for_project(project_id):
            try:
                existing_dim = embed_dimension(existing.embed_provider, existing.embed_model)
            except ValueError:
                # A legacy config on a model no longer in the map — cannot compare,
                # so do not block on it.
                continue
            if existing_dim != new_dim:
                raise EmbedDimensionConflict(
                    f"project already has configs at {existing_dim}-dim embeddings; "
                    f"{draft.embed_provider}:{draft.embed_model} is {new_dim}-dim"
                )

        if draft.embed_key_id is not None:
            await self._validate_embed_key(
                key_id=draft.embed_key_id,
                provider=draft.embed_provider,
                project_id=project_id,
            )
        if draft.rerank_enabled:
            if draft.rerank_key_id is None or not draft.rerank_provider:
                raise CapabilityMismatch("rerank_enabled=true requires rerank_key_id + rerank_provider")
            await self._validate_rerank_key(
                key_id=draft.rerank_key_id,
                provider=draft.rerank_provider,
                project_id=project_id,
            )

        # Durable, race-free project dimension pin (F-11). The live-sibling scan
        # above is a cheap pre-check; this is authoritative and serializes the
        # read-then-insert under an advisory lock so two concurrent first-creates
        # at different dimensions cannot both commit. It also gives File RAG a
        # stored per-project dimension that survives deleting the pinning config.
        await self._pins.ensure(
            project_id=project_id,
            kind=PinKind.FILE_RAG,
            provider=draft.embed_provider,
            model=draft.embed_model,
            dim=new_dim,
            on_conflict=lambda existing_dim, this_dim: EmbedDimensionConflict(
                f"project is pinned to {existing_dim}-dim embeddings; "
                f"{draft.embed_provider}:{draft.embed_model} is {this_dim}-dim"
            ),
        )

        cfg = await self._configs.create(
            project_id=project_id,
            name=draft.name,
            chunk_strategy=draft.chunk_strategy,
            chunk_params=draft.chunk_params,
            embed_key_id=draft.embed_key_id,
            embed_provider=draft.embed_provider,
            embed_model=draft.embed_model,
            rerank_enabled=draft.rerank_enabled,
            rerank_key_id=draft.rerank_key_id,
            rerank_provider=draft.rerank_provider,
            rerank_model=draft.rerank_model,
            top_k=draft.top_k,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.config_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_config",
                resource_id=cfg.id,
                metadata={
                    "project_id": str(project_id),
                    "name": cfg.name,
                    "chunk_strategy": cfg.chunk_strategy.value,
                    "embed_provider": cfg.embed_provider,
                    "embed_model": cfg.embed_model,
                    "rerank_enabled": cfg.rerank_enabled,
                },
                request_id=request_id,
            ),
        )
        return cfg

    async def update(
        self,
        *,
        config_id: uuid.UUID,
        patch: dict[str, Any],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> RagConfig:
        """Apply a partial update to a RAG config (mutable fields only)."""
        cfg = await self.get(config_id)  # 404 if missing

        # Validate rerank key if being changed or enabled.
        rerank_enabled = patch.get("rerank_enabled", cfg.rerank_enabled)
        if rerank_enabled:
            rerank_key_id = patch.get("rerank_key_id", cfg.rerank_key_id)
            rerank_provider = patch.get("rerank_provider", cfg.rerank_provider)
            if rerank_key_id is None or not rerank_provider:
                raise CapabilityMismatch("rerank_enabled=true requires rerank_key_id + rerank_provider")
            if "rerank_key_id" in patch or "rerank_provider" in patch:
                await self._validate_rerank_key(
                    key_id=rerank_key_id,
                    provider=rerank_provider,
                    project_id=cfg.project_id,
                )

        # Only allow mutable fields.
        mutable = {
            "name",
            "top_k",
            "chunk_params",
            "rerank_enabled",
            "rerank_key_id",
            "rerank_provider",
            "rerank_model",
        }
        db_values: dict[str, Any] = {}
        for k, v in patch.items():
            if k in mutable:
                db_values[k] = v

        updated = await self._configs.update(config_id, db_values)
        if updated is None:
            raise RagConfigNotFound(str(config_id))

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.config_updated",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_config",
                resource_id=config_id,
                metadata={
                    "project_id": str(cfg.project_id),
                    "changed_fields": list(db_values.keys()),
                },
                request_id=request_id,
            ),
        )
        return updated

    async def get(self, config_id: uuid.UUID) -> RagConfig:
        cfg = await self._configs.get(config_id)
        if cfg is None:
            raise RagConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[RagConfig]:
        return await self._configs.list_for_project(project_id)

    async def soft_delete(
        self,
        *,
        config_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Sequence[RagDocument]:
        """Soft-delete the config and hard-delete its child documents.

        DOM-1: a config delete must cascade. ``rag_documents`` are FK'd to
        ``rag_configs`` but Postgres does not cascade across a *soft* delete,
        so the document rows are hard-deleted here — ``rag_chunks`` follow
        via their own ``ON DELETE CASCADE`` FK. The deleted documents are
        returned (carrying ``minio_path``) so the caller can purge Qdrant
        points + MinIO blobs *after* the DB commit — infra cleanup must
        trail the durable audit row, never precede it (DOM-4).
        """
        cfg = await self.get(config_id)  # 404 if missing
        docs_repo = RagDocumentRepository(self._db)
        # Drain all child documents in batches — an unbounded single fetch
        # would OOM on very large configs, and the previous limit=10_000 cap
        # silently left orphan documents behind.
        page_size = 10_000
        docs: list[RagDocument] = []
        while True:
            batch = list(await docs_repo.list_for_config(config_id, limit=page_size))
            if not batch:
                break
            docs.extend(batch)
            for doc in batch:
                await docs_repo.delete(doc.id)
            if len(batch) < page_size:
                break
        if len(docs) >= 20_000:
            _log.warning(
                "soft_delete cascaded %d documents for config %s — consider async cleanup",
                len(docs),
                config_id,
            )
        # F-18: the DB ``ON DELETE SET NULL`` FK (migration 0012) unbinds attached
        # agents only on a physical row DELETE — this soft-delete is an UPDATE, so
        # the binding must be nulled explicitly. Cross-context write goes through
        # the agents facade (knowledge never writes ``agents`` directly), in this
        # same transaction so the unbind and the soft-delete commit atomically.
        # Nulling ``rag_config_id`` also disables each agent's File Search tool.
        # Local import: the agents context imports KnowledgeFacade, so a
        # module-level AgentsFacade import here would cycle.
        from contexts.agents.interfaces.facade import AgentsFacade

        unbound_agents = await AgentsFacade(self._db).clear_config_bindings(
            project_id=cfg.project_id,
            rag_config_id=config_id,
        )
        await self._configs.soft_delete(config_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.config_deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_config",
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

    async def clear_pin_if_last_config(self, *, project_id: uuid.UUID) -> bool:
        """Clear the durable File RAG pin iff no live config remains (F-11).

        Called in the delete's OWN transaction, before its commit, so the pin clear
        commits atomically with the soft-delete. Under the ``(project, file_rag)``
        advisory lock — held from here through that commit — a concurrent create
        blocks and never observes a config-deleted-but-pin-present state, closing
        the window where it would read the stale pin and be spuriously rejected at a
        different dimension (F-11 W1). Returns ``True`` when the pin was cleared,
        i.e. the project is now configless and ``rag_{project_id}`` is an orphan to
        drop after the commit.
        """
        await self._pins.acquire_lock(project_id, PinKind.FILE_RAG)
        if list(await self._configs.list_for_project(project_id)):
            return False
        await self._pins.clear(project_id=project_id, kind=PinKind.FILE_RAG)
        return True

    async def drop_orphan_collection(self, *, project_id: uuid.UUID) -> bool:
        """Drop ``rag_{project_id}`` after the delete commit, iff still configless (F-11).

        Runs post-commit (DOM-4: irreversible external deletes trail the durable DB
        commit, so a failed commit never leaves a dropped collection behind a live
        pin — W5). Re-acquires the ``(project, file_rag)`` lock and re-checks: if a
        concurrent create re-pinned the project between the two commits, its
        collection is left intact. Best-effort on the Qdrant side. Returns ``True``
        when it dropped.
        """
        await self._pins.acquire_lock(project_id, PinKind.FILE_RAG)
        if list(await self._configs.list_for_project(project_id)):
            return False

        from qdrant_client import AsyncQdrantClient

        from app.config.settings import get_settings
        from contexts.knowledge.infrastructure.qdrant_store import QdrantStore

        settings = get_settings()
        try:
            qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
            try:
                await QdrantStore(qclient).delete_collection(project_id)
            finally:
                await qclient.close()
        except Exception:
            _log.exception("rag drop-empty: qdrant collection drop failed for project %s", project_id)
        return True

    # ---- infrastructure operations ----------------------------------------

    @staticmethod
    async def purge_documents_infra(
        *,
        project_id: uuid.UUID,
        docs: Sequence[RagDocument],
    ) -> dict[str, Any]:
        """Best-effort removal of Qdrant points + MinIO blobs for ``docs``.

        MUST be called only *after* the DB transaction that removed the
        document rows has committed (DOM-4): infra deletes are irreversible,
        so the audit trail has to be durable first. Every failure is
        swallowed and reflected in the returned summary.
        """
        from qdrant_client import AsyncQdrantClient

        from app.config.settings import get_settings
        from contexts.knowledge.infrastructure.qdrant_store import QdrantStore

        summary: dict[str, Any] = {
            "documents": len(docs),
            "qdrant_purged": True,
            "blobs_removed": 0,
            "blobs_failed": 0,
        }
        if not docs:
            return summary

        settings = get_settings()

        # Qdrant points -- one batched delete keyed on doc_id.
        try:
            qclient = AsyncQdrantClient(
                url=settings.qdrant.url,
                api_key=settings.qdrant.api_key or None,
            )
            try:
                await QdrantStore(qclient).delete_documents(
                    project_id=project_id,
                    document_ids=[d.id for d in docs],
                )
            finally:
                await qclient.close()
        except Exception:
            summary["qdrant_purged"] = False
            _log.exception(
                "rag infra purge: qdrant delete failed for project %s",
                project_id,
            )

        # MinIO blobs -- one remove_object per document.
        minio_client = None
        try:
            from minio import Minio

            minio_client = Minio(
                settings.minio.endpoint,
                access_key=settings.minio.root_access_key,
                secret_key=settings.minio.root_secret_key,
                secure=settings.minio.use_tls,
                region=settings.minio.region,
            )
        except Exception:
            _log.exception("rag infra purge: minio client init failed")

        for d in docs:
            if minio_client is None:
                summary["blobs_failed"] += 1
                continue
            try:
                bucket, _, key = d.minio_path.partition("/")
                if bucket and key:
                    await asyncio.to_thread(
                        minio_client.remove_object,
                        bucket,
                        key,
                    )
                    summary["blobs_removed"] += 1
                else:
                    summary["blobs_failed"] += 1
            except Exception:
                summary["blobs_failed"] += 1
                _log.exception(
                    "rag infra purge: minio remove failed for doc %s",
                    d.id,
                )

        return summary

    @staticmethod
    def build_ingest_service(
        db: AsyncSession,
        *,
        embedder: Any,
    ) -> tuple[Any, Any]:
        """Construct an ``IngestService`` wired to real MinIO + Qdrant.

        Returns ``(ingest_service, qclient)`` — the caller MUST close
        ``qclient`` when done (typically via a try/finally block).
        """
        from qdrant_client import AsyncQdrantClient

        from app.config.settings import get_settings
        from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
        from contexts.knowledge.infrastructure.qdrant_store import QdrantStore

        from .ingest_service import IngestService

        settings = get_settings()

        from minio import Minio

        minio = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.root_access_key,
            secret_key=settings.minio.root_secret_key,
            secure=settings.minio.use_tls,
            region=settings.minio.region,
        )
        blob = MinioBlobStore(minio)

        qclient = AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=settings.qdrant.api_key or None,
        )
        qdrant = QdrantStore(qclient)

        ingest = IngestService(
            db,
            blob=blob,
            embedder=embedder,
            qdrant=qdrant,
            bucket=settings.minio.bucket_rag_sources,
        )
        return ingest, qclient


__all__ = ["RagConfigService"]
