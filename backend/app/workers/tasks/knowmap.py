"""Arq tasks for the Knowledge Map (Phase 3).

- ``knowmap_ingest_document`` — off-request parse/chunk/persist for a tus-registered
  document (large files must not chunk synchronously inside the final PATCH), then
  enqueues the graph build so the committed corpus change is reflected.
- ``knowmap_scan_document`` — ClamAV malware scan flipping ``scan_status``; a
  quarantine verdict triggers a rebuild so the graph drops the tainted document.

Mirrors ``app/workers/tasks/rag.py`` over the ``knowmap_*`` repositories.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.keys.infrastructure.adapters import build_router
from contexts.knowledge.application.embed_resolution import resolve_pinned_embed_key
from contexts.knowledge.application.graphrag_builder import (
    LOCK_TTL_S,
    EmbedderFactory,
    GraphRagBuilder,
    ResolvedEmbedder,
)
from contexts.knowledge.application.graphrag_ports import ConfigLike
from contexts.knowledge.application.knowmap_config_service import build_knowmap_embedder
from contexts.knowledge.application.knowmap_ingest_service import KnowmapIngestService
from contexts.knowledge.application.knowmap_triggers import enqueue_knowmap_build
from contexts.knowledge.domain.models import DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.embedders import router_embedder_for
from contexts.knowledge.infrastructure.knowmap_delta_loader import DocDeltaLoader
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from contexts.knowledge.infrastructure.knowmap_triple_extractor import DocTripleExtractor
from shared_kernel.db.session import get_sessionmaker

_log = logging.getLogger(__name__)

# The build lock (LOCK_TTL_S), refreshed per window, is the single-writer guard;
# the job timeout is only a runaway backstop and must have headroom over the TTL.
KNOWMAP_BUILD_TIMEOUT_S = LOCK_TTL_S * 3

# F-27: the scan task's arq retry budget. Named so the ClamAV-error SKIPPED path
# can enqueue a rebuild only once retries are exhausted (a document mid-retry might
# still come back CLEAN), and so the `.max_tries` assignment stays in sync.
_SCAN_MAX_TRIES = 3


async def _enqueue_rebuild_for_config(sm: Any, knowmap_config_id: uuid.UUID) -> None:
    """F-27: enqueue a full-corpus rebuild after a SKIPPED verdict, mirroring the
    QUARANTINED path, so the un-scannable document is removed from the buildable
    corpus. Under F-6's replacement semantics the rebuild also evicts the skipped
    document's triples from Neo4j. Reuses the ``knowmap_build_job_id`` dedup, so a
    SKIPPED enqueue coalesces with a concurrent quarantine/ingest enqueue for the
    same config+build cycle rather than multiplying builds."""
    async with sm() as db:
        cfg = await KnowmapConfigRepository(db).get(knowmap_config_id)
    if cfg is None:
        return
    await enqueue_knowmap_build(
        config_id=cfg.id, last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at
    )


async def _enqueue_build_on_clean(sm: Any, doc_id: uuid.UUID) -> None:
    """F-5: enqueue the graph build for a document whose scan just returned CLEAN,
    but only once it is READY.

    The mirror of the indexing side's clean-gate: if the document is not yet READY
    (async tus path, indexing still running), the index worker enqueues the build
    when it observes the clean verdict — last writer wins, so exactly one build is
    queued and a document is never left unbuilt (the dedup job id collapses a rare
    double). Re-reads the document fresh so it observes the indexing side's
    committed ``READY`` state.
    """
    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
        if doc is None or doc.status is not DocumentStatus.READY:
            return
        cfg = await KnowmapConfigRepository(db).get(doc.knowmap_config_id)
    if cfg is None:
        return
    await enqueue_knowmap_build(
        config_id=cfg.id, last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at
    )


async def knowmap_ingest_document(ctx: dict[str, Any], *, document_id: str) -> str:
    """Index one registered Knowledge Map document, then enqueue the build.

    Idempotent: re-runs on a document no longer in ``ingesting`` state are a no-op
    (``process_document`` guards)."""
    _ = ctx
    from minio import Minio

    from app.config.settings import get_settings

    doc_id = uuid.UUID(document_id)
    settings = get_settings()
    sm = get_sessionmaker()

    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
        if doc is None:
            _log.warning("knowmap_ingest_document: document %s not found", document_id)
            return f"document {document_id} not found"
        cfg = await KnowmapConfigRepository(db).get(doc.knowmap_config_id)
        if cfg is None:
            await KnowmapDocumentRepository(db).set_status(document_id=doc_id, status=DocumentStatus.FAILED)
            await db.commit()
            _log.warning("knowmap_ingest_document: config missing for %s", document_id)
            return "config missing"

        embedder = await build_knowmap_embedder(db, cfg)
        minio = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.root_access_key,
            secret_key=settings.minio.root_secret_key,
            secure=settings.minio.use_tls,
            region=settings.minio.region,
        )
        ingest = KnowmapIngestService(
            db,
            blob=MinioBlobStore(minio),
            embedder=embedder,
            bucket=settings.minio.bucket_knowmap_sources,
        )
        try:
            result = await ingest.process_document(document_id=doc_id)
            await db.commit()
        except Exception:
            await db.rollback()
            async with sm() as db2:
                current = await KnowmapDocumentRepository(db2).get(doc_id)
                if current is not None and current.status is not DocumentStatus.READY:
                    await KnowmapDocumentRepository(db2).set_status(
                        document_id=doc_id, status=DocumentStatus.FAILED
                    )
                    await db2.commit()
            _log.exception("knowmap_ingest_document failed for %s", document_id)
            raise

    # F-5: the build waits for BOTH readiness and a clean scan verdict. READY is now
    # committed; re-read the scan verdict (the scan worker commits it on its own
    # connection) and enqueue only if clean. If the scan is still pending, the scan
    # worker's clean-verdict path enqueues once it observes READY — last writer wins.
    async with sm() as db2:
        fresh = await KnowmapDocumentRepository(db2).get(doc_id)
    if fresh is not None and fresh.scan_status is ScanStatus.CLEAN:
        await enqueue_knowmap_build(
            config_id=cfg.id, last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at
        )
    return f"status={result.status.value} document={document_id}"


knowmap_ingest_document.max_tries = 3  # type: ignore[attr-defined]


async def knowmap_scan_document(ctx: dict[str, Any], *, document_id: str) -> str:
    """AV scan for a Knowledge Map document. Mirrors ``rag_scan_document``; a
    quarantine OR skipped verdict enqueues a rebuild so the un-scannable document
    leaves the buildable corpus (F-27)."""
    from app.config.settings import get_settings

    doc_id = uuid.UUID(document_id)
    sm = get_sessionmaker()

    if not get_settings().security.file_scan_enabled:
        async with sm() as db, db.begin():
            from shared_kernel.auth.clients import now

            await KnowmapDocumentRepository(db).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.CLEAN, scan_at=now()
            )
        # F-5: scan disabled == an immediate clean verdict; route through the shared
        # clean-verdict enqueue so the deferred build still fires once READY.
        await _enqueue_build_on_clean(sm, doc_id)
        return "clean"

    from shared_kernel.scanning import ScanError, get_scanner
    from shared_kernel.storage.minio_client import get_minio_client

    scanner = get_scanner()
    if scanner is None:
        raise RuntimeError("file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set")

    settings = get_settings()
    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
        if doc is None:
            _log.warning("knowmap_scan_document: document %s not found", document_id)
            return "not_found"

    if doc.size_bytes > settings.security.clamav_max_scan_bytes:
        from shared_kernel.auth.clients import now as _now2

        async with sm() as db2, db2.begin():
            await KnowmapDocumentRepository(db2).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.SKIPPED, scan_at=_now2()
            )
        # F-27: over-size is immediately terminal (no retry) → rebuild at once,
        # mirroring the QUARANTINED path, so the corpus excludes the skipped doc.
        await _enqueue_rebuild_for_config(sm, doc.knowmap_config_id)
        return "skipped:too_large"

    bucket, _, key = doc.minio_path.partition("/")
    minio = get_minio_client()
    data = await minio.get_object(bucket=bucket, key=key)

    try:
        result = await scanner.scan(data)
    except ScanError:
        _log.exception("knowmap_scan_document: ClamAV error for document %s", document_id)
        from shared_kernel.auth.clients import now as _now

        async with sm() as db2, db2.begin():
            await KnowmapDocumentRepository(db2).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.SKIPPED, scan_at=_now()
            )
        # F-27 (Q-2): enqueue the rebuild only once the retry budget is exhausted —
        # on a non-final attempt the document may still come back CLEAN, so a
        # premature rebuild would exclude a document that turns out fine. The task
        # re-raises either way so arq retries the scan. If arq does not populate
        # ``job_try`` (fallback per §7.2), default to the exhausted value so the
        # rebuild still fires (the dedup job id bounds the churn) rather than
        # silently never rebuilding.
        if ctx.get("job_try", _SCAN_MAX_TRIES) >= _SCAN_MAX_TRIES:
            await _enqueue_rebuild_for_config(sm, doc.knowmap_config_id)
        raise

    from shared_kernel import audit
    from shared_kernel.auth.clients import now

    scan_status = ScanStatus.CLEAN if result.clean else ScanStatus.QUARANTINED
    if not result.clean:
        _log.warning(
            "knowmap_scan_document: document %s quarantined — threat=%s",
            document_id,
            result.threat_name,
        )

    async with sm() as db:
        cfg = await KnowmapConfigRepository(db).get(doc.knowmap_config_id)
        async with db.begin():
            await KnowmapDocumentRepository(db).mark_scan(
                document_id=doc_id, scan_status=scan_status, scan_at=now()
            )
            if scan_status is ScanStatus.QUARANTINED:
                await audit.emit(
                    db,
                    audit.AuditEvent(
                        action="knowmap.document.quarantined",
                        resource_type="knowmap_document",
                        resource_id=doc_id,
                        metadata={
                            "scan_status": scan_status.value,
                            "threat_name": result.threat_name,
                        },
                    ),
                )
    # A quarantine changes the buildable corpus → rebuild so the tainted document's
    # triples leave the graph (retrieval already hides them via the allowed-doc gate).
    if scan_status is ScanStatus.QUARANTINED and cfg is not None:
        await enqueue_knowmap_build(
            config_id=cfg.id, last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at
        )
    elif scan_status is ScanStatus.CLEAN:
        # F-5: a clean verdict enqueues the deferred build — but only once the
        # document is READY (re-read fresh; last writer wins with the index worker).
        await _enqueue_build_on_clean(sm, doc_id)
    return scan_status.value


knowmap_scan_document.max_tries = _SCAN_MAX_TRIES  # type: ignore[attr-defined]


def _make_knowmap_embedder_factory(db: AsyncSession) -> EmbedderFactory:
    """EmbedderFactory selecting the key by the config's pinned embedding provider
    (Phase 2a D2) via the shared ``resolve_pinned_embed_key`` — the build,
    ingest, and retrieval paths resolve identically and cannot drift."""
    router = build_router(db)

    async def _factory(cfg: ConfigLike) -> ResolvedEmbedder:
        provider, model, key_id = await resolve_pinned_embed_key(db, cfg)
        embedder = router_embedder_for(
            router=router,
            key_id=key_id,
            project_id=cfg.project_id,
            provider=provider,
            model=model,
        )
        return ResolvedEmbedder(embedder=embedder, provider=provider, model=model)

    return _factory


async def knowmap_build(ctx: dict[str, Any], *, config_id: str, triggered_by: str = "manual") -> str:
    """Run a full Knowledge Map build for one config over the shared 2PC engine.

    Reuses the GraphRAG builder / Neo4j driver / snapshot + lock stores unchanged
    (R11.15); only the extractor (:class:`DocTripleExtractor`), the delta loader
    (:class:`DocDeltaLoader`), and the Qdrant collection prefix (``knowmap``) are
    forked. The Neo4j subgraph is scoped by the opaque config id; entity vectors
    land in ``knowmap_{project_id}``.
    """
    _ = ctx
    from qdrant_client import AsyncQdrantClient

    from app.config.settings import get_settings
    from contexts.knowledge.infrastructure.channels import knowmap_channel
    from contexts.knowledge.infrastructure.graphrag_vector_store import GraphRagVectorStore
    from contexts.knowledge.infrastructure.neo4j_driver import Neo4jAsyncDriver
    from contexts.knowledge.infrastructure.redis_lock import (
        RedisBuildLockStore,
        RedisSnapshotStore,
    )

    cfg_id = uuid.UUID(config_id)
    settings = get_settings()

    neo4j = Neo4jAsyncDriver(uri=settings.neo4j.url, auth=(settings.neo4j.user, settings.neo4j.password))
    qclient = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
    try:
        vector_store = GraphRagVectorStore(qclient, prefix="knowmap")
        sm = get_sessionmaker()
        async with sm() as db:
            configs = KnowmapConfigRepository(db)
            cfg = await configs.get(cfg_id)
            if cfg is None:
                _log.warning("knowmap_build: config %s not found", config_id)
                return f"config {config_id} not found"

            builder = GraphRagBuilder(
                db=db,
                neo4j=neo4j,
                vector_store=vector_store,
                extractor=DocTripleExtractor(router=build_router(db)),
                lock_store=RedisBuildLockStore(),
                snapshot_store=RedisSnapshotStore(),
                delta_loader=DocDeltaLoader(),
                embedder_factory=_make_knowmap_embedder_factory(db),
                configs=configs,
                channel_fn=knowmap_channel,
            )
            try:
                # F-6: Knowledge Map builds are full-corpus and use replacement
                # semantics — the loader re-reads the whole surviving corpus each
                # build, so relations/entities/vectors absent from the current
                # corpus are removed and evidence/provenance is recomputed to the
                # live corpus. Concept Map delta builds pass replace=False.
                result = await builder.run(config_id=cfg_id, triggered_by=triggered_by, replace=True)
                await db.commit()
            except Exception:
                _log.exception("knowmap_build failed config=%s", config_id)
                raise
            _log.info(
                "knowmap_build done config=%s state=%s triples=%d entities=%d",
                config_id,
                result.state.value,
                result.triples_written,
                result.entities_written,
            )
            return (
                f"state={result.state.value} "
                f"triples={result.triples_written} entities={result.entities_written}"
            )
    finally:
        await neo4j.close()
        await qclient.close()


__all__ = ["knowmap_build", "knowmap_ingest_document", "knowmap_scan_document"]
