"""Finalise a completed tus ``purpose=rag_source`` upload (Phase E.6).

This is the *register* half of large-file RAG ingestion (R10.02 / R22.15):
the tus PATCH layer has filled a staging file on disk; we stream it into the
``rag-sources`` bucket, create the ``rag_documents`` row in ``ingesting``
state, and enqueue the ``rag_ingest_document`` Arq worker which runs the
parse/chunk/embed/upsert pipeline off the request path (``IngestService
.process_document``). Files up to 1 GiB must never embed synchronously inside
the final PATCH.

The byte layout matches the synchronous multipart path
(``rag-sources/{project_id}/{config_id}/{sha256}``) so both ingestion routes
store, dedup, and download blobs identically.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.ingest_service import (
    _normalise_mime,
    emit_reupload_audit,
    rag_source_object_key,
)
from contexts.knowledge.domain.errors import RagConfigNotFound, UnsupportedMime
from contexts.knowledge.domain.models import DocumentStatus, RagDocument
from contexts.knowledge.infrastructure.parsers import MIME_TO_PARSER
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.queue import enqueue
from contexts.knowledge.infrastructure.channels import rag_channel
from shared_kernel.realtime.pubsub import Publisher
from shared_kernel.storage import get_minio_client

_SHA_BLOCK = 1024 * 1024  # 1 MiB streaming read — never loads the whole file


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_SHA_BLOCK), b""):
            h.update(block)
    return h.hexdigest()


class RagTusFinalizer:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = RagConfigRepository(db)
        self._docs = RagDocumentRepository(db)
        self._minio = get_minio_client()

    async def finalize(
        self,
        *,
        rag_config_id: uuid.UUID,
        filename: str,
        mime: str,
        staging_path: str,
        size_bytes: int,
        uploaded_by: uuid.UUID | None,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> RagDocument:
        norm_mime = _normalise_mime(mime, filename)
        if norm_mime not in MIME_TO_PARSER:
            raise UnsupportedMime(f"mime {norm_mime!r} not in {{pdf,docx,md,txt}}")

        cfg = await self._configs.get(rag_config_id)
        if cfg is None:
            raise RagConfigNotFound(str(rag_config_id))

        # Stream-hash the staged file (R10.02 dedup) without loading 1 GiB into
        # the web process.
        sha = await asyncio.to_thread(_sha256_file, staging_path)
        existing = await self._docs.find_by_sha(rag_config_id=cfg.id, sha256=sha)
        if existing is not None and existing.status is DocumentStatus.READY:
            # Same bytes already indexed for this config — true dedup; the caller
            # cleans up the staging file.
            return existing
        if existing is not None:
            # A prior attempt left this sha FAILED/stuck; the blob is already in
            # MinIO. Re-drive the worker rather than dedup onto a dead row so a
            # re-upload is a genuine retry. Record the re-upload in the audit trail
            # (the first upload is long past).
            await emit_reupload_audit(
                self._db,
                doc=existing,
                actor_user_id=uploaded_by,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            await self._enqueue_index(existing.id, config_id=cfg.id)
            return existing

        key = rag_source_object_key(project_id=cfg.project_id, config_id=cfg.id, sha256=sha)
        await self._minio.put_file(
            bucket=self._minio.rag_sources_bucket,
            key=key,
            file_path=staging_path,
            content_type=norm_mime,
        )
        doc = await self._docs.create(
            rag_config_id=cfg.id,
            filename=filename,
            mime=norm_mime,
            size_bytes=size_bytes,
            sha256=sha,
            minio_path=f"{self._minio.rag_sources_bucket}/{key}",
            uploaded_by=uploaded_by,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.document_uploaded",
                actor_user_id=uploaded_by,
                actor_ip=actor_ip,
                resource_type="rag_document",
                resource_id=doc.id,
                metadata={
                    "rag_config_id": str(cfg.id),
                    "filename": filename,
                    "mime": norm_mime,
                    "size_bytes": size_bytes,
                    "sha256": sha,
                    "via": "tus",
                },
                request_id=request_id,
            ),
        )
        await self._enqueue_index(doc.id, config_id=cfg.id)
        return doc

    async def _enqueue_index(self, document_id: uuid.UUID, *, config_id: uuid.UUID) -> None:
        # Commit the rag_documents row BEFORE enqueuing: the rag_ingest_document
        # worker runs on a separate connection and must see a committed row. The
        # request's db_session dependency only commits AFTER the handler returns,
        # so without this the worker can dequeue first, find no row, and the doc
        # would stick in 'ingesting' forever (db_session docstring warns of this).
        await self._db.commit()
        # ws:rag:{config_id} — register-phase event; the worker emits the terminal
        # ingestion.completed/.failed once indexing runs.
        await Publisher(rag_channel(config_id)).emit(
            "ingestion.started", {"document_id": str(document_id), "total": 1}
        )
        # Deterministic job id: collapses a duplicate/concurrent enqueue for the
        # same document (e.g. a re-upload while the first job is still running) to
        # one run, so two workers never index the same doc and collide on
        # uq_rag_chunk_doc_idx. Arq's own retry of a *failed* run reuses this id
        # (it is not a new enqueue), so transient failures still retry; only a
        # fresh manual re-upload within the result-TTL window is briefly deduped.
        try:
            await enqueue(
                "rag_ingest_document",
                document_id=str(document_id),
                _job_id=f"rag-ingest:{document_id}",
            )
        except Exception:
            # Arq/Redis unavailable: don't leave the committed row stuck
            # 'ingesting' with no worker. Mark it FAILED so a re-upload (re-drive)
            # or operator can retry, and tell the frontend.
            await self._docs.set_status(document_id=document_id, status=DocumentStatus.FAILED)
            await self._db.commit()
            with contextlib.suppress(Exception):
                await Publisher(rag_channel(config_id)).emit(
                    "ingestion.failed",
                    {"document_id": str(document_id), "error": "could not enqueue indexing job"},
                )
            raise


__all__ = ["RagTusFinalizer"]
