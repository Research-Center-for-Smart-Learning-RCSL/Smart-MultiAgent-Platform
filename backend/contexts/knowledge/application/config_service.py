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
from contexts.knowledge.domain.embedding_pin import PinKind, TeardownOutcome
from contexts.knowledge.domain.errors import (
    CapabilityMismatch,
    ChunkParamsImmutable,
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
    normalized_chunk_params,
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


def _purge_bucket_prefix(mc: Any, bucket: str, prefix: str) -> int:
    """Remove every object under ``prefix`` in ``bucket``; return the count (F-24).

    Synchronous (runs in a worker thread via ``asyncio.to_thread``): the MinIO SDK
    is blocking. ``remove_object_sync`` is idempotent on a missing key.
    """
    removed = 0
    for obj in mc.list_objects_sync(bucket, prefix=prefix):
        mc.remove_object_sync(bucket, obj.object_name)
        removed += 1
    return removed


def _list_bucket_prefixes(mc: Any, bucket: str) -> list[Any]:
    """List only the top-level ``{project_id}/`` prefixes of ``bucket`` (F-24).

    Non-recursive so a bucket with millions of objects still yields one entry per
    project instead of the full object listing. Runs in a worker thread.
    """
    return list(mc.list_objects_sync(bucket, recursive=False))


def _resolve_qdrant_store(qdrant_store: Any) -> tuple[Any, Any]:
    """Return ``(store, client_to_close)`` for a File RAG Qdrant teardown (F-24).

    When ``qdrant_store`` is supplied it is reused and its client is left open
    (the caller owns the lifecycle, so a batch shares one client); when ``None`` a
    short-lived client is built and returned as the second element for the caller
    to close in a ``finally``.
    """
    if qdrant_store is not None:
        return qdrant_store, None

    from qdrant_client import AsyncQdrantClient

    from app.config.settings import get_settings
    from contexts.knowledge.infrastructure.qdrant_store import QdrantStore

    settings = get_settings()
    qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
    return QdrantStore(qclient), qclient


class RagConfigService:
    def __init__(self, db: AsyncSession, *, bge_reranker_url: str | None = None) -> None:
        self._db = db
        self._configs = RagConfigRepository(db)
        self._documents = RagDocumentRepository(db)
        self._pins = EmbeddingPinRepository(db)
        self._keys_facade = KeysFacade(db)
        # F-19: whether the bundled local reranker is deployed, injected by the
        # app layer (application must not read app.config). Empty/None => the
        # keyless "bge" rerank option is rejected with a doc-pointing error.
        self._bge_reranker_url = bge_reranker_url

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

    async def _validate_rerank_selection(
        self,
        *,
        provider: str | None,
        key_id: uuid.UUID | None,
        project_id: uuid.UUID,
    ) -> None:
        """Validate an enabled rerank selection, branching on provider (F-19).

        - ``"bge"`` — the keyless bundled local reranker: it must NOT carry a key
          and the bundled service must be configured (``bge_reranker_url`` set).
          Routes entirely around :meth:`_validate_rerank_key` (and F-1's
          carried-key project-scope check) — there is no key to scope.
        - ``"cohere"`` (any BYO-key provider) — requires a key + capability, via
          :meth:`_validate_rerank_key`.
        """
        if not provider:
            raise CapabilityMismatch("rerank_enabled=true requires a rerank_provider")
        if provider == "bge":
            if key_id is not None:
                raise CapabilityMismatch(
                    "rerank_provider 'bge' is a keyless local reranker — do not supply rerank_key_id"
                )
            if not self._bge_reranker_url:
                raise CapabilityMismatch(
                    "local bge reranker selected but no bundled reranker service is configured; "
                    "deploy the bge-reranker service and set RERANK_BGE_URL (see [R10.08])"
                )
            return
        if key_id is None:
            raise CapabilityMismatch("rerank_enabled=true requires rerank_key_id + rerank_provider")
        await self._validate_rerank_key(key_id=key_id, provider=provider, project_id=project_id)

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
            await self._validate_rerank_selection(
                provider=draft.rerank_provider,
                key_id=draft.rerank_key_id,
                project_id=project_id,
            )

        # F-3: a previous delete may have left a teardown owed — its pin retained
        # because Qdrant could not confirm the drop. Retry it before `ensure` reads
        # the pin, so this create is not rejected against a collection no live config
        # is using. No-op (and no Qdrant call) unless the pin is a different
        # dimension and the project is configless.
        await self._retry_pending_teardown(project_id, new_dim)

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

        # Validate the rerank selection when being changed or enabled (F-19: the
        # provider may be the keyless "bge", which legitimately carries no key).
        rerank_enabled = patch.get("rerank_enabled", cfg.rerank_enabled)
        if rerank_enabled:
            rerank_key_id = patch.get("rerank_key_id", cfg.rerank_key_id)
            rerank_provider = patch.get("rerank_provider", cfg.rerank_provider)
            if "rerank_key_id" in patch or "rerank_provider" in patch:
                # The selection is being (re)touched — full validation, including
                # the key capability / project-scope check for a BYO-key provider.
                await self._validate_rerank_selection(
                    provider=rerank_provider,
                    key_id=rerank_key_id,
                    project_id=cfg.project_id,
                )
            else:
                # Unrelated edit (e.g. top_k) on an already-enabled config — cheap
                # shape guard only, tolerating the keyless bge provider; never
                # re-hit the key DB/scope check for an unchanged selection.
                if not rerank_provider:
                    raise CapabilityMismatch("rerank_enabled=true requires a rerank_provider")
                if rerank_provider != "bge" and rerank_key_id is None:
                    raise CapabilityMismatch("rerank_enabled=true requires rerank_key_id + rerank_provider")

        # F-20 (R10.04): chunk params are fixed once the config has any locking
        # document. Chunking is a live read at each ingest with no per-document
        # provenance, so a change after documents exist would split the corpus
        # between two policies. Reject only a *changing* patch (Q-2: the full-form
        # save always resends chunk_params, so an identical no-op must pass); the
        # normalized compare tolerates absent-vs-default keys and key order.
        if (
            "chunk_params" in patch
            and normalized_chunk_params(cfg.chunk_strategy, patch["chunk_params"])
            != normalized_chunk_params(cfg.chunk_strategy, cfg.chunk_params)
            and await self._documents.count_locking_for_config(config_id) > 0
        ):
            raise ChunkParamsImmutable(
                f"config {config_id} has documents; chunk parameters are fixed once "
                f"a corpus exists (delete all documents to re-tune, or recreate the config)"
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
        docs_repo = self._documents
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

    async def teardown_orphan_collection(self, *, project_id: uuid.UUID) -> TeardownOutcome:
        """Drop ``rag_{project_id}`` and release the pin, iff configless (F-3/F-11).

        Runs post-commit, so DOM-4 still holds: the irreversible external delete
        trails the durable soft-delete commit. What changed for F-3 is the *pin*,
        which now trails the drop instead of preceding it. The pin is released only
        on ``DROPPED``/``ABSENT`` — i.e. only once Qdrant has confirmed the
        collection is gone. A Qdrant error yields ``FAILED`` and keeps the pin, so an
        incompatible config stays rejected rather than being accepted against a
        collection that is still standing at the old dimension.

        This knowingly re-opens W5 in the narrow window where the drop succeeds but
        this transaction's commit does not: a dropped collection behind a live pin.
        That is the deliberate trade (spec Q-4) — a stale pin only over-rejects and
        is reclaimed by the next retry that observes ``ABSENT``, whereas the old
        ordering failed open and let the project's fixed dimension be violated with
        no recovery.

        Holds the ``(project, file_rag)`` lock across the Qdrant call so a concurrent
        create serializes against it; the call is bounded by
        ``qdrant.teardown_timeout_s`` so a hung Qdrant cannot hold the lock open.
        """
        await self._pins.acquire_lock(project_id, PinKind.FILE_RAG)
        if list(await self._configs.list_for_project(project_id)):
            return TeardownOutcome.SKIPPED_LIVE_CONFIG

        from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
        from contexts.knowledge.infrastructure.qdrant_teardown import delete_collection_bounded

        try:
            dropped = await delete_collection_bounded(
                lambda client: QdrantStore(client).delete_collection(project_id)
            )
        except Exception as exc:
            # The exception class, never the exception: qdrant-client errors carry
            # response content and headers, and this log line is not the place to
            # find out what is in them. The class still separates a timeout from an
            # auth failure from a 500, which is what triage needs.
            _log.error(
                "rag teardown: qdrant collection drop failed for project %s (%s); "
                "pin retained, collection will be retried",
                project_id,
                type(exc).__name__,
            )
            return TeardownOutcome.FAILED

        await self._pins.clear(project_id=project_id, kind=PinKind.FILE_RAG)
        return TeardownOutcome.DROPPED if dropped else TeardownOutcome.ABSENT

    async def _retry_pending_teardown(self, project_id: uuid.UUID, new_dim: int) -> None:
        """Retry a teardown a previous delete left owed, so this create is not
        wrongly rejected by its retained pin (F-3, spec Q-5).

        Fires only when the pin sits at a *different* dimension and no live config
        remains — precisely the state a failed teardown leaves behind, and precisely
        the create that would otherwise be rejected against a collection nobody is
        using. A create whose dimension matches the pin needs no teardown and takes
        no Qdrant call, so ordinary config CRUD stays independent of Qdrant
        availability (spec §2). On failure this returns quietly and lets the caller's
        ``ensure`` raise the typed conflict, which is the pre-F-3 behavior.
        """
        await self._pins.acquire_lock(project_id, PinKind.FILE_RAG)
        pin = await self._pins.get(project_id, PinKind.FILE_RAG)
        if pin is None or int(pin.dim) == new_dim:
            return
        await self.teardown_orphan_collection(project_id=project_id)

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
    async def purge_project_source_infra(
        *,
        project_id: uuid.UUID,
        qdrant_store: Any = None,
    ) -> dict[str, Any]:
        """Row-independent teardown of a project's source infra (F-24).

        Erases, keyed on ``project_id`` alone (never the ``rag_documents`` rows,
        which have already cascaded away at tenant hard-delete):

        * every File RAG source blob under the ``{project_id}/`` prefix of the
          ``rag-sources`` bucket, and every Knowledge Map source blob under the
          same prefix of the ``knowmap-sources`` bucket (Q-5);
        * the File RAG per-project ``rag_{project_id}`` Qdrant collection.

        Knowledge/Concept Map *graph* vectors (Neo4j + ``graphrag_*`` Qdrant) are
        NOT touched here — those stay F-8's domain. Best-effort and isolated per
        store: a failure on one store is logged and reflected in the summary
        without aborting the others. Idempotent — a re-run when the data is
        already gone is a no-op. Emits no audit; the caller records erasure.

        ``qdrant_store`` may be an already-constructed :class:`QdrantStore` shared
        across a batch (its client is then owned by the caller and left open);
        when ``None`` a short-lived client is built and closed here.
        ``collection_dropped`` reflects whether a collection actually existed, not
        merely that the drop call succeeded.
        """
        from shared_kernel.storage import get_minio_client

        summary: dict[str, Any] = {
            "project_id": str(project_id),
            "blobs_removed": 0,
            "buckets_failed": 0,
            "collection_dropped": False,
        }

        mc = get_minio_client()
        # Exact ``{project_id}/`` prefix (a bare project-UUID segment) so one
        # project's prefix can never match another's — UUIDs are fixed-length and
        # differ before the trailing slash.
        prefix = f"{project_id}/"
        for bucket in (mc.rag_sources_bucket, mc.knowmap_sources_bucket):
            try:
                summary["blobs_removed"] += await asyncio.to_thread(_purge_bucket_prefix, mc, bucket, prefix)
            except Exception:
                summary["buckets_failed"] += 1
                _log.exception(
                    "rag source teardown: prefix purge failed for bucket %s, project %s",
                    bucket,
                    project_id,
                )

        store, qclient = _resolve_qdrant_store(qdrant_store)
        try:
            summary["collection_dropped"] = await store.delete_collection(project_id)
        except Exception:
            _log.exception("rag source teardown: qdrant collection drop failed for project %s", project_id)
        finally:
            if qclient is not None:
                await qclient.close()

        return summary

    @staticmethod
    async def list_rag_source_orphans(
        *,
        live_project_ids: set[uuid.UUID],
        qdrant_store: Any = None,
    ) -> set[uuid.UUID]:
        """Project ids whose source infra is orphaned — no ``projects`` row (F-24).

        Enumerates candidates directly from the external stores (so already-leaked
        data from before this fix is discoverable) and returns those absent from
        ``live_project_ids``, the set of *every* ``projects`` id regardless of
        ``deleted_at`` (Q-4): a soft-deleted-but-not-hard-deleted project still has
        a row and must keep its data until hard-delete.

        Candidates come from both source buckets (the distinct top-level prefix of
        each key, listed non-recursively so the sweep does not materialise every
        object) and from the ``rag_{project_id}`` Qdrant collections (compared
        against *expected* names to avoid dash/underscore ambiguity). Only a
        canonically-spelled UUID segment/name is accepted (round-trip guard), so a
        foreign or non-canonical key the teardown could never actually purge is not
        flagged into a perpetual re-sweep. Enumeration of a store that raises is
        logged and skipped for this cycle — never fatal. ``qdrant_store`` may be a
        shared store (see :meth:`purge_project_source_infra`).
        """
        from shared_kernel.storage import get_minio_client

        orphans: set[uuid.UUID] = set()

        mc = get_minio_client()
        for bucket in (mc.rag_sources_bucket, mc.knowmap_sources_bucket):
            try:
                # Non-recursive: one entry per top-level ``{project_id}/`` prefix.
                objects = await asyncio.to_thread(_list_bucket_prefixes, mc, bucket)
            except Exception:
                _log.exception("rag orphan sweep: list_objects failed for bucket %s", bucket)
                continue
            for obj in objects:
                segment = (getattr(obj, "object_name", "") or "").split("/", 1)[0]
                try:
                    pid = uuid.UUID(segment)
                except ValueError:
                    continue
                # Round-trip guard: only a canonically-spelled segment maps to a
                # ``{pid}/`` prefix the teardown can actually purge; a non-canonical
                # spelling (e.g. dash-less) would otherwise be re-flagged every sweep.
                if str(pid) != segment:
                    continue
                if pid not in live_project_ids:
                    orphans.add(pid)

        from contexts.knowledge.infrastructure.qdrant_store import collection_name

        store, qclient = _resolve_qdrant_store(qdrant_store)
        try:
            names = await store.list_collection_names()
        except Exception:
            _log.exception("rag orphan sweep: qdrant list_collections failed")
            names = []
        finally:
            if qclient is not None:
                await qclient.close()

        expected = {collection_name(pid) for pid in live_project_ids}
        for name in names:
            if not name.startswith("rag_") or name in expected:
                continue
            try:
                pid = uuid.UUID(name[len("rag_") :].replace("_", "-"))
            except ValueError:
                continue
            # Round-trip guard: only act on a name our own normaliser produces, so a
            # foreign ``rag_*`` collection is never over-deleted. ``name not in
            # expected`` above already excluded every live project's collection, so a
            # name that round-trips here is provably not live.
            if collection_name(pid) != name:
                continue
            orphans.add(pid)

        return orphans

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
