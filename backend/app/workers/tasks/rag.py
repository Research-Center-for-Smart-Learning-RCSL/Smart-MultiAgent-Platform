"""Arq task: rag_ingest_document — E.6 off-request RAG indexing (R10.02).

A completed tus ``purpose=rag_source`` upload is registered by
``RagTusFinalizer`` (blob streamed to MinIO + ``rag_documents`` row in
``ingesting`` state) and this worker runs the heavy parse/chunk/embed/upsert
pipeline via ``IngestService.process_document`` — large files (up to 1 GiB)
must not embed synchronously inside the final PATCH.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import Any

from minio import Minio
from qdrant_client import AsyncQdrantClient

from app.config.settings import get_settings
from contexts.keys.infrastructure.adapters import build_router
from contexts.knowledge.application.ingest_service import IngestService
from contexts.knowledge.domain.models import DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.channels import rag_channel
from contexts.knowledge.infrastructure.embedders import router_embedder_for
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)


async def rag_ingest_document(ctx: dict[str, Any], *, document_id: str) -> str:
    """Index one registered RAG document. Idempotent: re-runs on a document no
    longer in ``ingesting`` state are a no-op (``process_document`` guards)."""
    _ = ctx
    doc_id = uuid.UUID(document_id)
    settings = get_settings()
    sm = get_sessionmaker()

    async with sm() as db:
        doc = await RagDocumentRepository(db).get(doc_id)
        if doc is None:
            _log.warning("rag_ingest_document: document %s not found", document_id)
            return f"document {document_id} not found"
        cfg = await RagConfigRepository(db).get(doc.rag_config_id)
        if cfg is None or cfg.embed_key_id is None:
            await RagDocumentRepository(db).set_status(document_id=doc_id, status=DocumentStatus.FAILED)
            await db.commit()
            # Emit the terminal event the frontend waits on; without it the docs
            # table sticks on 'ingesting' until a manual reload.
            with contextlib.suppress(Exception):
                await Publisher(rag_channel(doc.rag_config_id)).emit(
                    "ingestion.failed",
                    {"document_id": document_id, "error": "rag config missing or has no embed key"},
                )
            _log.warning("rag_ingest_document: config missing/embed_key_id unset for %s", document_id)
            return "config missing or no embed key"

        embedder = router_embedder_for(
            router=build_router(db),
            key_id=cfg.embed_key_id,
            provider=cfg.embed_provider,
            model=cfg.embed_model,
        )
        minio = Minio(
            settings.minio.endpoint,
            access_key=settings.minio.root_access_key,
            secret_key=settings.minio.root_secret_key,
            secure=settings.minio.use_tls,
            region=settings.minio.region,
        )
        qclient = AsyncQdrantClient(
            url=settings.qdrant.url,
            api_key=settings.qdrant.api_key or None,
        )
        ingest = IngestService(
            db,
            blob=MinioBlobStore(minio),
            embedder=embedder,
            qdrant=QdrantStore(qclient),
            bucket=settings.minio.bucket_rag_sources,
        )
        try:
            result = await ingest.process_document(document_id=doc_id)
            await db.commit()
            return f"status={result.status.value} document={document_id}"
        except Exception:
            # process_document already emitted ingestion.failed (Redis, survives
            # the rollback) but its set_status(FAILED) rode the doomed txn. Persist
            # the terminal status in a fresh session so the doc never sticks in
            # 'ingesting', then re-raise so Arq retries — process_document
            # reprocesses any non-READY doc, so a retry of a transient failure
            # genuinely re-indexes (it is not a dead no-op).
            await db.rollback()
            async with sm() as db2:
                # Don't clobber a doc another run already brought to READY (e.g. a
                # uq_rag_chunk_doc_idx collision from a duplicate run that lost the
                # race): only mark FAILED if it is still mid-flight.
                current = await RagDocumentRepository(db2).get(doc_id)
                if current is not None and current.status is not DocumentStatus.READY:
                    await RagDocumentRepository(db2).set_status(
                        document_id=doc_id, status=DocumentStatus.FAILED
                    )
                    await db2.commit()
            _log.exception("rag_ingest_document failed for %s", document_id)
            raise
        finally:
            await qclient.close()


# P20: The task re-raises after marking FAILED so that Arq retries on
# transient failures (provider 429, Qdrant timeout, etc.).  Arq's default
# max_tries is 1 (no retry); set it to 3 so we get 2 automatic retries
# before the job lands in the dead-letter queue.  Arq reads this attribute
# from the function object at job dispatch time.
rag_ingest_document.max_tries = 3  # type: ignore[attr-defined]

async def rag_scan_document(ctx: dict[str, Any], *, document_id: str) -> str:
    """AV scan for a RAG document (R22.15.07). Mirrors file_scan_requested."""
    _ = ctx

    if not get_settings().security.file_scan_enabled:
        doc_id = uuid.UUID(document_id)
        sm = get_sessionmaker()
        async with sm() as db, db.begin():
            from shared_kernel.auth.clients import now

            await RagDocumentRepository(db).mark_scan(
                document_id=doc_id,
                scan_status=ScanStatus.CLEAN,
                scan_at=now(),
            )
        return "clean"

    from shared_kernel.scanning import ScanError, get_scanner
    from shared_kernel.storage.minio_client import get_minio_client

    scanner = get_scanner()
    if scanner is None:
        raise RuntimeError(
            "file_scan_enabled is True but SMAP_SEC_CLAMAV_HOST is not set"
        )

    settings = get_settings()
    doc_id = uuid.UUID(document_id)
    sm = get_sessionmaker()
    async with sm() as db:
        doc = await RagDocumentRepository(db).get(doc_id)
        if doc is None:
            _log.warning("rag_scan_document: document %s not found", document_id)
            return "not_found"

    if doc.size_bytes > settings.security.clamav_max_scan_bytes:
        _log.warning(
            "rag_scan_document: document %s skipped — %d bytes exceeds scan limit %d",
            document_id, doc.size_bytes, settings.security.clamav_max_scan_bytes,
        )
        from shared_kernel.auth.clients import now as _now2

        async with sm() as db2, db2.begin():
            await RagDocumentRepository(db2).mark_scan(
                document_id=doc_id, scan_status=ScanStatus.SKIPPED, scan_at=_now2(),
            )
        return "skipped:too_large"

    bucket, key = doc.minio_path.split("/", 1)
    minio = get_minio_client()
    data = await minio.get_object(bucket=bucket, key=key)

    try:
        result = await scanner.scan(data)
    except ScanError:
        _log.exception("rag_scan_document: ClamAV error for document %s", document_id)
        from shared_kernel.auth.clients import now as _now

        async with sm() as db2, db2.begin():
            await RagDocumentRepository(db2).mark_scan(
                document_id=doc_id,
                scan_status=ScanStatus.SKIPPED,
                scan_at=_now(),
            )
        raise

    from shared_kernel import audit
    from shared_kernel.auth.clients import now

    scan_status = ScanStatus.CLEAN if result.clean else ScanStatus.QUARANTINED
    if not result.clean:
        _log.warning(
            "rag_scan_document: document %s quarantined — threat=%s",
            document_id,
            result.threat_name,
        )

    async with sm() as db, db.begin():
        await RagDocumentRepository(db).mark_scan(
            document_id=doc_id,
            scan_status=scan_status,
            scan_at=now(),
        )
        if scan_status is ScanStatus.QUARANTINED:
            await audit.emit(
                db,
                audit.AuditEvent(
                    action="rag.document.quarantined",
                    resource_type="rag_document",
                    resource_id=doc_id,
                    metadata={
                        "scan_status": scan_status.value,
                        "threat_name": result.threat_name,
                    },
                ),
            )
    return scan_status.value


rag_scan_document.max_tries = 3  # type: ignore[attr-defined]

__all__ = ["rag_ingest_document", "rag_scan_document"]
