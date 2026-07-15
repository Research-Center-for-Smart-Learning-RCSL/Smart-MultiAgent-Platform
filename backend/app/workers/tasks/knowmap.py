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
from contexts.knowledge.domain.graphrag import BuildState
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


async def _bump_and_enqueue_build(sm: Any, knowmap_config_id: uuid.UUID) -> None:
    """Advance the corpus revision for a scan-verdict event that changes the
    buildable corpus, then enqueue a build for the new revision (F-12 W1).

    A scan verdict flipping a document into ``ready∧clean`` (or a QUARANTINED /
    SKIPPED verdict that must exclude it) changes what the next build processes,
    but — unlike an add/reprocess/delete — does not itself go through the
    document-mutation bump. Without advancing the revision here, two documents
    uploaded together whose scans clear against the same pre-scan revision compute
    the same ``knowmap:build:{config}:{revision}`` id, and arq drops the second as
    a duplicate, silently leaving it unbuilt — the exact race F-12 closes for the
    add path, reopened for the scan-verdict edge. Bumping gives each such event a
    distinct target so it is never suppressed. ``bump_corpus_revision`` returns 0
    only when the config was concurrently deleted — nothing to build."""
    async with sm() as db, db.begin():
        new_rev = await KnowmapConfigRepository(db).bump_corpus_revision(knowmap_config_id)
    if new_rev == 0:
        return
    await enqueue_knowmap_build(config_id=knowmap_config_id, target_revision=new_rev)


async def _enqueue_rebuild_for_config(sm: Any, knowmap_config_id: uuid.UUID) -> None:
    """F-27: enqueue a full-corpus rebuild after a SKIPPED verdict, mirroring the
    QUARANTINED path, so the un-scannable document is removed from the buildable
    corpus. Under F-6's replacement semantics the rebuild also evicts the skipped
    document's triples from Neo4j. Advances the corpus revision (F-12 W1) so the
    removal build is never dropped as a duplicate of a retained prior build."""
    await _bump_and_enqueue_build(sm, knowmap_config_id)


async def _enqueue_build_on_clean(sm: Any, doc_id: uuid.UUID, *, entered: bool) -> None:
    """F-5: enqueue the graph build for a document whose scan just returned CLEAN,
    but only once it is READY.

    ``entered`` is True only when this verdict newly adds the document to the
    ready∧clean set (its prior scan status was not CLEAN). A CLEAN->CLEAN reconfirm
    (a reindex rescan) leaves membership unchanged: any content change was already
    enqueued by the ingest side's immediate clean-build, and advancing the revision
    here would double it (F-12 W3), so this returns without touching the queue.

    The mirror of the indexing side's clean-gate: if the document is not yet READY
    (async tus path, indexing still running), the index worker enqueues the build
    when it observes the clean verdict — last writer wins, so exactly one build is
    queued and a document is never left unbuilt. Re-reads the document fresh so it
    observes the indexing side's committed ``READY`` state, then advances the
    corpus revision (F-12 W1) so this clean-set entry gets a build id distinct from
    a sibling upload's.
    """
    if not entered:
        return
    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
        if doc is None or doc.status is not DocumentStatus.READY:
            return
        cfg_id = doc.knowmap_config_id
    await _bump_and_enqueue_build(sm, cfg_id)


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
        fresh_cfg = await KnowmapConfigRepository(db2).get(cfg.id)
    if fresh is not None and fresh.scan_status is ScanStatus.CLEAN and fresh_cfg is not None:
        # F-12: target the corpus revision the ingest commit bumped (re-read fresh).
        await enqueue_knowmap_build(config_id=cfg.id, target_revision=fresh_cfg.corpus_revision)
    return f"status={result.status.value} document={document_id}"


knowmap_ingest_document.max_tries = 3  # type: ignore[attr-defined]


async def knowmap_scan_document(ctx: dict[str, Any], *, document_id: str) -> str:
    """AV scan for a Knowledge Map document. Mirrors ``rag_scan_document``; a
    quarantine OR skipped verdict enqueues a rebuild so the un-scannable document
    leaves the buildable corpus (F-27)."""
    from app.config.settings import get_settings

    doc_id = uuid.UUID(document_id)
    sm = get_sessionmaker()

    async with sm() as db:
        doc = await KnowmapDocumentRepository(db).get(doc_id)
    if doc is None:
        _log.warning("knowmap_scan_document: document %s not found", document_id)
        return "not_found"
    # F-12 (W1/W6): the buildable ready∧clean set only *changes* when a verdict
    # crosses the CLEAN boundary. A reconfirming CLEAN->CLEAN (a reindex rescan)
    # does not add the document, and a QUARANTINED/SKIPPED verdict on a document
    # that was never CLEAN removes nothing — so neither advances the revision nor
    # triggers a rebuild. Only a real membership change does.
    prior_clean = doc.scan_status is ScanStatus.CLEAN

    if not get_settings().security.file_scan_enabled:
        async with sm() as db, db.begin():
            from shared_kernel.auth.clients import now

            await KnowmapDocumentRepository(db).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.CLEAN, scan_at=now()
            )
        # F-5: scan disabled == an immediate clean verdict; route through the shared
        # clean-verdict enqueue so the deferred build still fires once READY.
        await _enqueue_build_on_clean(sm, doc_id, entered=not prior_clean)
        return "clean"

    from shared_kernel.scanning import ScanError, get_scanner
    from shared_kernel.storage.minio_client import get_minio_client

    scanner = get_scanner()
    if scanner is None:
        raise RuntimeError("file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set")

    settings = get_settings()
    if doc.size_bytes > settings.security.clamav_max_scan_bytes:
        from shared_kernel.auth.clients import now as _now2

        async with sm() as db2, db2.begin():
            await KnowmapDocumentRepository(db2).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.SKIPPED, scan_at=_now2()
            )
        # F-27: over-size is immediately terminal (no retry). Rebuild only if the
        # document was already CLEAN (hence possibly built) — a fresh over-size
        # document was never in the buildable set, so no rebuild is needed (F-12 W6).
        if prior_clean:
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

        # Mark SKIPPED immediately on every attempt, so an unscannable document is
        # excluded from the buildable/retrieval set right away and is never left
        # non-terminal by a retry that is interrupted before it completes. The task
        # still re-raises so arq retries the scan; if a later attempt recovers a
        # CLEAN verdict, the clean-verdict path below re-adds the document.
        async with sm() as db2, db2.begin():
            await KnowmapDocumentRepository(db2).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.SKIPPED, scan_at=_now()
            )
        # F-27 (Q-2): evict on the CLEAN->SKIPPED transition, mirroring the
        # QUARANTINED path. ``prior_clean`` is read fresh from ``scan_status`` at
        # entry (before this write), so it is True only on the attempt that first
        # removes a previously-CLEAN (hence possibly built) document from the
        # buildable set — later retries read SKIPPED and never re-enqueue. A
        # never-CLEAN document has no triples in the graph to evict (F-12 W6).
        # (The earlier ``job_try >= _SCAN_MAX_TRIES`` guard could never fire: the
        # first attempt's SKIPPED write made ``prior_clean`` read False by the
        # final attempt, so the eviction never ran.)
        if prior_clean:
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
    # A quarantine of a document that was CLEAN (hence possibly built) changes the
    # buildable corpus → rebuild so its triples leave the graph (retrieval already
    # hides them via the allowed-doc gate). Advance the revision (F-12 W1) so the
    # removal build is never dropped. A never-CLEAN document has no triples in the
    # graph, so nothing to evict (F-12 W6).
    if scan_status is ScanStatus.QUARANTINED and cfg is not None and prior_clean:
        await _bump_and_enqueue_build(sm, cfg.id)
    elif scan_status is ScanStatus.CLEAN:
        # F-5: a clean verdict enqueues the deferred build — but only once the
        # document is READY (re-read fresh; last writer wins with the index worker).
        # Advance the revision only when the document is newly *entering* the
        # ready∧clean set; a CLEAN->CLEAN reconfirm is handled by the ingest side.
        await _enqueue_build_on_clean(sm, doc_id, entered=not prior_clean)
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


async def knowmap_build(
    ctx: dict[str, Any],
    *,
    config_id: str,
    triggered_by: str = "manual",
    target_revision: int | None = None,
) -> str:
    """Run a full Knowledge Map build for one config over the shared 2PC engine.

    Reuses the GraphRAG builder / Neo4j driver / snapshot + lock stores unchanged
    (R11.15); only the extractor (:class:`DocTripleExtractor`), the delta loader
    (:class:`DocDeltaLoader`), and the Qdrant collection prefix (``knowmap``) are
    forked. The Neo4j subgraph is scoped by the opaque config id; entity vectors
    land in ``knowmap_{project_id}``.

    ``target_revision`` (F-12) is the ``corpus_revision`` this build was enqueued
    for. On success it is recorded as ``built_corpus_revision``; if the corpus
    advanced past it *during* the build (a mutation committed after the delta
    snapshot), a single follow-up build is enqueued for the newer revision so no
    committed change is ever left unbuilt. ``None`` for a legacy in-flight job
    enqueued before this deploy — those skip the re-check and complete normally.
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
        # F-12: record the processed revision and enqueue one follow-up if the
        # corpus advanced during the build (a mutation committed after the delta
        # snapshot). Runs in its own session — the shared builder stays
        # enqueue-free so layer boundaries hold and Concept Maps are unaffected.
        # Best-effort: the build already committed its terminal state above, so a
        # transient error in this post-build bookkeeping must not fail the job and
        # trigger arq to re-run the entire (already-successful) build. A missed
        # follow-up is recovered by the next document change or a manual rebuild.
        try:
            await _finalize_build_revision(
                sm, cfg_id, target_revision, succeeded=result.state is BuildState.IDLE
            )
        except Exception:
            _log.exception("knowmap_build: post-build revision finalize failed config=%s", config_id)
        return (
            f"state={result.state.value} "
            f"triples={result.triples_written} entities={result.entities_written}"
        )
    finally:
        await neo4j.close()
        await qclient.close()


async def _finalize_build_revision(
    sm: Any,
    config_id: uuid.UUID,
    target_revision: int | None,
    *,
    succeeded: bool,
) -> int | None:
    """Record the built revision and re-enqueue if the corpus advanced (F-12).

    On a successful build, stamp ``built_corpus_revision = target_revision`` and
    re-read the current ``corpus_revision``; if it advanced past the target while
    the build ran, enqueue exactly one follow-up for the newer revision (which
    itself deduplicates on that revision, so the chain terminates once the built
    revision catches up). No-op for a failed build or a legacy job with no
    ``target_revision``. Returns the follow-up revision enqueued, or ``None``."""
    if not succeeded or target_revision is None:
        return None
    async with sm() as db:
        configs = KnowmapConfigRepository(db)
        async with db.begin():
            await configs.set_built_corpus_revision(config_id, target_revision)
        current = await configs.get(config_id)
    if current is not None and current.corpus_revision > target_revision:
        await enqueue_knowmap_build(config_id=config_id, target_revision=current.corpus_revision)
        return current.corpus_revision
    return None


__all__ = ["knowmap_build", "knowmap_ingest_document", "knowmap_scan_document"]
