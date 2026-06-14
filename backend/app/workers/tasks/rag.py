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
from contexts.knowledge.domain.models import DocumentStatus
from contexts.knowledge.infrastructure.blob_store import MinioBlobStore
from contexts.knowledge.infrastructure.embedders import router_embedder_for
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime.pubsub import Publisher, rag_channel

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


__all__ = ["rag_ingest_document"]
