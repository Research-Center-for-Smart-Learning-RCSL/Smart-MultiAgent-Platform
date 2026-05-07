"""Document ingestion pipeline (R10.01 – R10.04, R10.11).

Steps:
  1. MIME + size gate (R10.03, E.5 32 MB hard limit for multipart).
  2. SHA-256 dedup within the same rag_config (R10.02).
  3. Persist bytes to MinIO bucket `rag-sources` under
     ``{project_id}/{config_id}/{sha256}.{ext}``.
  4. Insert `rag_documents` with ``status='ingesting'``.
  5. Parse → chunk → embed → insert `rag_chunks` → upsert Qdrant.
  6. Flip `rag_documents.status` to `ready` on success.

Failure semantics:
  Any exception in the parse/chunk/embed/upsert stage is re-raised as
  :class:`IngestFailed`. The enclosing request transaction rolls back,
  so the ``rag_documents`` row and Qdrant points are discarded in the
  same unit of work. The MinIO blob is orphaned by design — reuploading
  the same bytes overwrites it at the deterministic ``sha256`` key, so
  retry is idempotent at the storage layer. The ``set_status(FAILED)``
  call before re-raising is a best-effort marker that only survives if
  the caller runs the service in a sub-transaction; in the default
  request-scoped transaction it is rolled back with the rest.

SoC:
- The service owns the *happy path and audit trail*.
- Parsing / chunking live in `infrastructure` helpers.
- Qdrant / MinIO / embedder boundaries are injected as Protocols so tests
  can swap them without touching production code.
- Virus scanning (R10.01 ClamAV) is surfaced via the
  ``rag_documents.scan_status`` column; the nightly ClamAV worker flips
  it, and retrieval filters out non-`clean` rows. Ingest marks fresh
  uploads as ``scan_status='pending'`` by table default.
"""

from __future__ import annotations

import contextlib
import hashlib
import mimetypes
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.ports import BlobStore, Embedder
from contexts.knowledge.domain.errors import (
    DocumentTooLarge,
    IngestFailed,
    RagConfigNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.models import DocumentStatus, RagDocument
from contexts.knowledge.infrastructure.chunkers import chunk_text
from contexts.knowledge.infrastructure.parsers import MIME_TO_PARSER
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.knowledge.infrastructure.repositories import (
    RagChunkRepository,
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit

MAX_MULTIPART_BYTES = 32 * 1024 * 1024  # §22.7 — tus for anything larger


@dataclass(frozen=True, slots=True)
class IngestInput:
    rag_config_id: uuid.UUID
    filename: str
    mime: str
    data: bytes
    uploaded_by: uuid.UUID | None


class IngestService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        blob: BlobStore,
        embedder: Embedder,
        qdrant: QdrantStore,
        bucket: str = "rag-sources",
    ) -> None:
        self._db = db
        self._blob = blob
        self._embedder = embedder
        self._qdrant = qdrant
        self._bucket = bucket
        self._configs = RagConfigRepository(db)
        self._docs = RagDocumentRepository(db)
        self._chunks = RagChunkRepository(db)

    async def ingest(
        self,
        *,
        ipt: IngestInput,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> RagDocument:
        if len(ipt.data) > MAX_MULTIPART_BYTES:
            raise DocumentTooLarge(f"multipart upload exceeds {MAX_MULTIPART_BYTES} bytes; use tus")

        mime = _normalise_mime(ipt.mime, ipt.filename)
        if mime not in MIME_TO_PARSER:
            raise UnsupportedMime(f"mime {mime!r} not in {{pdf,docx,md,txt}}")

        cfg = await self._configs.get(ipt.rag_config_id)
        if cfg is None:
            raise RagConfigNotFound(str(ipt.rag_config_id))

        sha = hashlib.sha256(ipt.data).hexdigest()

        # Dedup per R10.02: same sha in same config → return the existing row
        # without re-embedding.
        existing = await self._docs.find_by_sha(
            rag_config_id=cfg.id,
            sha256=sha,
        )
        if existing is not None:
            return existing

        # Persist bytes first so a crash mid-pipeline never leaves a DB row
        # pointing at a missing blob.
        key = f"{cfg.project_id}/{cfg.id}/{sha}"
        minio_path = await self._blob.put(
            bucket=self._bucket,
            key=key,
            data=ipt.data,
            content_type=mime,
        )

        doc = await self._docs.create(
            rag_config_id=cfg.id,
            filename=ipt.filename,
            mime=mime,
            size_bytes=len(ipt.data),
            sha256=sha,
            minio_path=minio_path,
            uploaded_by=ipt.uploaded_by,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="rag.document_uploaded",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="rag_document",
                resource_id=doc.id,
                metadata={
                    "rag_config_id": str(cfg.id),
                    "filename": ipt.filename,
                    "mime": mime,
                    "size_bytes": len(ipt.data),
                    "sha256": sha,
                },
                request_id=request_id,
            ),
        )

        try:
            text = MIME_TO_PARSER[mime](ipt.data)
            pieces = chunk_text(
                text,
                strategy=cfg.chunk_strategy,
                params=cfg.chunk_params,
            )
            if pieces:
                vectors = await self._embedder.embed_batch(pieces)
                await self._qdrant.ensure_collection(
                    cfg.project_id,
                    vector_size=self._embedder.vector_size,
                )
                point_ids: list[uuid.UUID] = [uuid.uuid4() for _ in pieces]
                # Insert DB rows before upserting Qdrant vectors: if insert_many
                # fails the transaction rolls back cleanly before Qdrant is touched.
                # If upsert_chunks fails after a successful insert_many, the
                # transaction rolls back the DB rows so no orphaned records remain.
                await self._chunks.insert_many(
                    [
                        {
                            "document_id": doc.id,
                            "chunk_idx": idx,
                            "text": pieces[idx],
                            "qdrant_point_id": point_ids[idx],
                        }
                        for idx in range(len(pieces))
                    ]
                )
                await self._qdrant.upsert_chunks(
                    project_id=cfg.project_id,
                    points=[
                        (
                            pid,
                            vec,
                            {
                                "doc_id": str(doc.id),
                                "chunk_idx": idx,
                                "agent_ids": [],
                            },
                        )
                        for idx, (pid, vec) in enumerate(zip(point_ids, vectors, strict=False))
                    ],
                )
            await self._docs.set_status(
                document_id=doc.id,
                status=DocumentStatus.READY,
            )
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="rag.document_indexed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="rag_document",
                    resource_id=doc.id,
                    metadata={"chunks": len(pieces)},
                    request_id=request_id,
                ),
            )
        except Exception as exc:  # — any failure → mark + surface
            # Best-effort status flip; if this fails the enclosing transaction
            # rolls back anyway, dropping the row in the same unit of work.
            with contextlib.suppress(Exception):
                await self._docs.set_status(
                    document_id=doc.id,
                    status=DocumentStatus.FAILED,
                )
            raise IngestFailed(f"{type(exc).__name__}: {exc}") from exc

        # Re-read to return the committed status.
        refreshed = await self._docs.get(doc.id)
        assert refreshed is not None
        return refreshed


def _normalise_mime(raw: str, filename: str) -> str:
    """Prefer the client-supplied MIME; fall back to filename sniff."""
    if raw and raw not in {"application/octet-stream", ""}:
        return raw
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


__all__ = ["IngestInput", "IngestService", "MAX_MULTIPART_BYTES"]
