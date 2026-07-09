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

from contexts.knowledge.application.knowmap_config_service import build_knowmap_embedder
from contexts.knowledge.application.knowmap_ingest_service import KnowmapIngestService
from contexts.knowledge.application.knowmap_triggers import enqueue_knowmap_build
from contexts.knowledge.domain.models import DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from shared_kernel.db.session import get_sessionmaker

_log = logging.getLogger(__name__)


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

    # Corpus changed and is committed → enqueue the graph build (dedup job id).
    await enqueue_knowmap_build(
        config_id=cfg.id, last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at
    )
    return f"status={result.status.value} document={document_id}"


knowmap_ingest_document.max_tries = 3  # type: ignore[attr-defined]


async def knowmap_scan_document(ctx: dict[str, Any], *, document_id: str) -> str:
    """AV scan for a Knowledge Map document. Mirrors ``rag_scan_document``; a
    quarantine verdict enqueues a rebuild so the graph drops the tainted document."""
    _ = ctx
    from app.config.settings import get_settings

    doc_id = uuid.UUID(document_id)
    sm = get_sessionmaker()

    if not get_settings().security.file_scan_enabled:
        async with sm() as db, db.begin():
            from shared_kernel.auth.clients import now

            await KnowmapDocumentRepository(db).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.CLEAN, scan_at=now()
            )
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
    return scan_status.value


knowmap_scan_document.max_tries = 3  # type: ignore[attr-defined]

__all__ = ["knowmap_ingest_document", "knowmap_scan_document"]
