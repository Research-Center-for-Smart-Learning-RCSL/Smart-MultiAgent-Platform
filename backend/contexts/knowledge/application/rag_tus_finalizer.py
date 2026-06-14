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
import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.ingest_service import _normalise_mime
from contexts.knowledge.domain.errors import RagConfigNotFound, UnsupportedMime
from contexts.knowledge.domain.models import RagDocument
from contexts.knowledge.infrastructure.parsers import MIME_TO_PARSER
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.queue import enqueue
from shared_kernel.realtime.pubsub import Publisher, rag_channel
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
        if existing is not None:
            # Same bytes already ingested for this config — skip re-upload + the
            # worker enqueue; the caller cleans up the staging file.
            return existing

        key = f"{cfg.project_id}/{cfg.id}/{sha}"
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
        # ws:rag:{config_id} — register-phase event; the worker emits the
        # terminal ingestion.completed/.failed once indexing runs.
        await Publisher(rag_channel(cfg.id)).emit(
            "ingestion.started", {"document_id": str(doc.id), "total": 1}
        )
        # Off-request indexing (E.6): the worker downloads the blob and runs the
        # parse/chunk/embed/upsert pipeline. Job id is keyed on the document so a
        # duplicate enqueue collapses to one run.
        await enqueue(
            "rag_ingest_document",
            document_id=str(doc.id),
            _job_id=f"rag-ingest:{doc.id}",
        )
        return doc


__all__ = ["RagTusFinalizer"]
