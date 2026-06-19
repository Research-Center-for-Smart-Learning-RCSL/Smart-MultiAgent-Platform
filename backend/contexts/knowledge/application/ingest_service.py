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
from contexts.knowledge.domain.models import DocumentStatus, RagConfig, RagDocument
from contexts.knowledge.infrastructure.channels import rag_channel
from contexts.knowledge.infrastructure.chunkers import chunk_text
from contexts.knowledge.infrastructure.parsers import MIME_TO_PARSER
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.knowledge.infrastructure.repositories import (
    RagChunkRepository,
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher

MAX_MULTIPART_BYTES = 32 * 1024 * 1024  # §22.7 — tus for anything larger

# Embed + persist this many chunks per round-trip. Bounds the provider request
# size (avoids 413 on a huge document) and the peak vector memory to one batch,
# so a 1 GiB tus upload does not hold every vector at once.
_EMBED_BATCH = 128


def rag_source_object_key(*, project_id: uuid.UUID, config_id: uuid.UUID, sha256: str) -> str:
    """Canonical MinIO key for a RAG source blob — shared by the synchronous
    multipart path and the async tus finaliser so both write/dedup/download at
    the same location (sha-addressed for idempotent re-upload)."""
    return f"{project_id}/{config_id}/{sha256}"


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

        # Dedup per R10.02: same sha in same config. Only a *successful* prior
        # ingest short-circuits — a FAILED/stuck row is re-indexed in place so a
        # re-upload is a genuine retry rather than a no-op onto a dead row.
        existing = await self._docs.find_by_sha(
            rag_config_id=cfg.id,
            sha256=sha,
        )
        if existing is not None and existing.status is DocumentStatus.READY:
            return existing
        if existing is not None:
            # Re-upload of a FAILED/stuck doc — record it in the audit trail (the
            # first upload is long past) so retries aren't invisible, then re-index.
            await emit_reupload_audit(
                self._db,
                doc=existing,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            await Publisher(rag_channel(cfg.id)).emit(
                "ingestion.started", {"document_id": str(existing.id), "total": 1}
            )
            return await self._index_document(
                doc=existing,
                cfg=cfg,
                data=ipt.data,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        # Persist bytes first so a crash mid-pipeline never leaves a DB row
        # pointing at a missing blob.
        key = rag_source_object_key(project_id=cfg.project_id, config_id=cfg.id, sha256=sha)
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
        # Live status for clients watching ws:rag:{config_id} (useRagConfigSocket).
        # Multipart ingest is synchronous (one doc per request), so we emit the
        # start/terminal events only — there is no incremental progress to report.
        # Fire-and-forget: the frontend refetches authoritative state on receipt.
        await Publisher(rag_channel(cfg.id)).emit(
            "ingestion.started", {"document_id": str(doc.id), "total": 1}
        )

        return await self._index_document(
            doc=doc,
            cfg=cfg,
            data=ipt.data,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def process_document(
        self,
        *,
        document_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> RagDocument:
        """Index an already-registered document (E.6 async tus path).

        The tus finaliser (``RagTusFinalizer``) has already streamed the bytes
        to MinIO and created the ``rag_documents`` row in ``ingesting`` state +
        emitted ``ingestion.started``. The ``rag_ingest_document`` Arq worker
        calls this to download the blob and run the parse/chunk/embed/upsert
        pipeline off the request path — large files (up to 1 GiB) must not embed
        synchronously inside the final PATCH.
        """
        doc = await self._docs.get(document_id)
        if doc is None:
            raise IngestFailed(f"document {document_id} not found")
        if doc.status is DocumentStatus.READY:
            # Already indexed — idempotent no-op (a duplicate enqueue or a retry
            # after success). A FAILED/INGESTING doc is (re)processed so an Arq
            # retry of a transient failure actually re-indexes.
            return doc
        cfg = await self._configs.get(doc.rag_config_id)
        if cfg is None:
            raise RagConfigNotFound(str(doc.rag_config_id))

        bucket, _, key = doc.minio_path.partition("/")
        data = await self._blob.get(bucket=bucket, key=key)
        return await self._index_document(
            doc=doc,
            cfg=cfg,
            data=data,
            actor_user_id=doc.uploaded_by,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def _index_document(
        self,
        *,
        doc: RagDocument,
        cfg: RagConfig,
        data: bytes,
        actor_user_id: uuid.UUID | None,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> RagDocument:
        """Parse → chunk → embed → upsert for a registered document, then flip
        status + emit the terminal ws event. Shared by the synchronous
        multipart ``ingest`` path and the async ``process_document`` worker path
        so both index identically. The caller owns registration + the
        ``ingestion.started`` event."""
        try:
            text = MIME_TO_PARSER[doc.mime](data)
            pieces = chunk_text(
                text,
                strategy=cfg.chunk_strategy,
                params=cfg.chunk_params,
            )
            await self._qdrant.ensure_collection(
                cfg.project_id,
                vector_size=self._embedder.vector_size,
            )
            # Idempotent reprocess: clear any chunks + Qdrant points from a prior
            # (failed) attempt before re-inserting. delete_document filters by the
            # doc_id payload, so it also sweeps points orphaned by a rolled-back
            # batch. _index_document only runs on non-READY docs (READY short-
            # circuits earlier), which never have *committed* chunks, so this is a
            # no-op on the fresh path and a clean-slate on retry/re-upload — and it
            # prevents uq_rag_chunk_doc_idx collisions on reprocess.
            await self._qdrant.delete_document(project_id=cfg.project_id, document_id=doc.id)
            await self._chunks.delete_for_document(doc.id)
            if pieces:
                # Embed + persist in batches: a tus rag_source can be up to 1 GiB
                # → hundreds of thousands of chunks. Sending them all to the
                # embedder in one call risks a provider 413 and holds every vector
                # in memory at once. On a mid-document failure the DB rolls back
                # every rag_chunks row; earlier batches' Qdrant points are left
                # behind but are swept by the clear-then-index above on the next
                # attempt (delete_document by doc_id), so they never accumulate.
                total_chunks = len(pieces)
                pub = Publisher(rag_channel(cfg.id))
                for start in range(0, total_chunks, _EMBED_BATCH):
                    batch = pieces[start : start + _EMBED_BATCH]
                    vectors = await self._embedder.embed_batch(batch)
                    if len(vectors) != len(batch):
                        # DOM-5: refuse a short vector list that would leave
                        # trailing chunks with no Qdrant point (silently
                        # unretrievable) while the count still reports full.
                        raise ValueError(
                            f"embedder returned {len(vectors)} vectors for "
                            f"{len(batch)} chunks; refusing partial index"
                        )
                    # Emit progress so the frontend progress bar updates in
                    # real time (P19 — ingestion.progress was never emitted).
                    processed = min(start + len(batch), total_chunks)
                    await pub.emit(
                        "ingestion.progress",
                        {
                            "document_id": str(doc.id),
                            "processed": processed,
                            "total": total_chunks,
                        },
                    )
                    point_ids: list[uuid.UUID] = [uuid.uuid4() for _ in batch]
                    # Insert DB rows before the Qdrant upsert so a DB failure
                    # rolls back before Qdrant is touched.
                    await self._chunks.insert_many(
                        [
                            {
                                "document_id": doc.id,
                                "chunk_idx": start + i,
                                "text": batch[i],
                                "qdrant_point_id": point_ids[i],
                            }
                            for i in range(len(batch))
                        ]
                    )
                    # Signal the transition from embedding to Qdrant upsert
                    # (P19 — ingestion.indexing was never emitted).
                    await pub.emit(
                        "ingestion.indexing",
                        {"document_id": str(doc.id), "batch_start": start},
                    )
                    await self._qdrant.upsert_chunks(
                        project_id=cfg.project_id,
                        points=[
                            (
                                pid,
                                vec,
                                {
                                    "doc_id": str(doc.id),
                                    "chunk_idx": start + i,
                                    "agent_ids": [],
                                },
                            )
                            for i, (pid, vec) in enumerate(zip(point_ids, vectors, strict=True))
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
            await Publisher(rag_channel(cfg.id)).emit(
                "ingestion.completed", {"document_id": str(doc.id), "chunks": len(pieces)}
            )
        except Exception as exc:  # — any failure → mark + surface
            # Best-effort status flip; if this fails the enclosing transaction
            # rolls back anyway, dropping the row in the same unit of work.
            with contextlib.suppress(Exception):
                await self._docs.set_status(
                    document_id=doc.id,
                    status=DocumentStatus.FAILED,
                )
            with contextlib.suppress(Exception):
                await Publisher(rag_channel(cfg.id)).emit(
                    "ingestion.failed", {"document_id": str(doc.id), "error": str(exc)}
                )
            raise IngestFailed(f"{type(exc).__name__}: {exc}") from exc

        # Re-read so the returned row reflects the just-set status (the caller
        # owns the commit).
        refreshed = await self._docs.get(doc.id)
        assert refreshed is not None
        return refreshed


async def emit_reupload_audit(
    db: AsyncSession,
    *,
    doc: RagDocument,
    actor_user_id: uuid.UUID | None,
    actor_ip: str | None,
    request_id: uuid.UUID | None,
) -> None:
    """Audit a re-upload of an existing (non-READY) RAG document. The original
    ``rag.document_uploaded`` is long past; without this, retries leave only
    ``rag.document_indexed`` rows and the upload trail under-counts re-uploads.
    Shared by the multipart re-index path and the tus re-drive path."""
    await audit.emit(
        db,
        audit.AuditEvent(
            action="rag.document_uploaded",
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            resource_type="rag_document",
            resource_id=doc.id,
            metadata={
                "rag_config_id": str(doc.rag_config_id),
                "filename": doc.filename,
                "mime": doc.mime,
                "size_bytes": doc.size_bytes,
                "sha256": doc.sha256,
                "reupload": True,
            },
            request_id=request_id,
        ),
    )


def _normalise_mime(raw: str, filename: str) -> str:
    """Prefer the client-supplied MIME; fall back to filename sniff."""
    # Strip MIME parameters (e.g. "; charset=utf-8") so downstream lookups
    # match the bare media type (M6).
    raw = raw.split(";")[0].strip()
    if raw and raw not in {"application/octet-stream", ""}:
        return raw
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


__all__ = ["IngestInput", "IngestService", "MAX_MULTIPART_BYTES"]
