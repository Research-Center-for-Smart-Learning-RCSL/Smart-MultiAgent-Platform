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
  A parse failure (unreadable / unsupported document) is surfaced as
  :class:`DocumentUnprocessable` (422); any other failure in the
  chunk/embed/upsert stage as :class:`IngestFailed` (500). The document row is
  committed before indexing (multipart) or already committed (tus / reindex), so
  on failure the partial chunk writes are rolled back and the row is committed
  ``FAILED`` — it stays visible in the list instead of vanishing. Qdrant points
  from partial batches are swept immediately. The MinIO blob is sha-addressed, so
  a re-upload overwrites it and retry is idempotent at the storage layer.

SoC:
- The service owns the *happy path and audit trail*.
- Parsing / chunking live in `infrastructure` helpers.
- Qdrant / MinIO / embedder boundaries are injected as Protocols so tests
  can swap them without touching production code.
- Virus scanning (R10.01 ClamAV) is surfaced via the
  ``rag_documents.scan_status`` column; the nightly ClamAV worker flips
  it, and retrieval withholds ``quarantined`` rows (``pending`` stays
  retrievable so a fresh upload has no availability gap). Ingest marks
  fresh uploads as ``scan_status='pending'`` by table default.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.ports import BlobStore, Embedder
from contexts.knowledge.domain.errors import (
    DocumentTooLarge,
    DocumentUnprocessable,
    IngestFailed,
    RagConfigNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.models import DocumentStatus, RagConfig, RagDocument
from contexts.knowledge.infrastructure.channels import rag_channel
from contexts.knowledge.infrastructure.chunkers import chunk_document
from contexts.knowledge.infrastructure.qdrant_store import QdrantStore
from contexts.knowledge.infrastructure.repositories import (
    RagChunkRepository,
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher
from shared_kernel.text_extraction.parsers import MIME_TO_PARSER, ParserError, normalise_mime

_log = logging.getLogger(__name__)

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
    # Per-agent allowlist for the new document (empty = no agent may retrieve
    # it). The API layer validates these belong to the config before ingest.
    agent_ids: tuple[uuid.UUID, ...] = ()


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

        mime = normalise_mime(ipt.mime, ipt.filename)
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
            reindexed = await self._index_document(
                doc=existing,
                cfg=cfg,
                data=ipt.data,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            await self._db.commit()
            await enqueue_rag_scan(document_id=reindexed.id)
            return reindexed

        # Persist bytes first so a crash mid-pipeline never leaves a DB row
        # pointing at a missing blob.
        key = rag_source_object_key(project_id=cfg.project_id, config_id=cfg.id, sha256=sha)
        minio_path = await self._blob.put(
            bucket=self._bucket,
            key=key,
            data=ipt.data,
            content_type=mime,
        )

        try:
            doc = await self._docs.create(
                rag_config_id=cfg.id,
                filename=ipt.filename,
                mime=mime,
                size_bytes=len(ipt.data),
                sha256=sha,
                minio_path=minio_path,
                uploaded_by=ipt.uploaded_by,
                agent_ids=ipt.agent_ids,
            )
        except IntegrityError:
            # Concurrent ingest of the same (rag_config_id, sha256) won the
            # find_by_sha -> create race. Roll back the failed insert and resolve
            # to the winner (dedup) instead of surfacing a 500. The blob is keyed
            # by sha, so the put above was an idempotent overwrite.
            await self._db.rollback()
            existing = await self._docs.find_by_sha(rag_config_id=cfg.id, sha256=sha)
            if existing is not None:
                return existing
            raise
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

        # Commit the accepted upload before indexing so an index failure leaves a
        # durable FAILED row (see _index_document) instead of rolling the whole
        # upload back to nothing — a failed upload must stay visible in the list.
        await self._db.commit()
        result = await self._index_document(
            doc=doc,
            cfg=cfg,
            data=ipt.data,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        await self._db.commit()
        await enqueue_rag_scan(document_id=result.id)
        return result

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
            pieces = await chunk_document(
                text,
                strategy=cfg.chunk_strategy,
                params=cfg.chunk_params,
                embedder=self._embedder,
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
            # Sweep any Qdrant points written by partial batches of this attempt
            # NOW (not just on a future retry), so an abandoned document — one
            # never re-uploaded — does not leak vectors forever. Filtered by
            # doc_id, so it touches only this document's points.
            with contextlib.suppress(Exception):
                await self._qdrant.delete_document(project_id=cfg.project_id, document_id=doc.id)
            # Persist FAILED durably. The row is committed before indexing (multipart
            # ingest()) or already committed (tus finaliser / reindex), so rolling back
            # the partial chunk writes and committing FAILED keeps the document visible
            # as FAILED instead of vanishing from the list.
            with contextlib.suppress(Exception):
                await self._db.rollback()
                await self._docs.set_status(
                    document_id=doc.id,
                    status=DocumentStatus.FAILED,
                )
                await self._db.commit()
            with contextlib.suppress(Exception):
                await Publisher(rag_channel(cfg.id)).emit(
                    "ingestion.failed", {"document_id": str(doc.id), "error": str(exc)}
                )
            # A parse failure is a client-fixable input problem (unparseable, no text
            # layer, or unsupported content) → 422; any other failure (embedding,
            # provider, store) is a server-side ingest failure → 500.
            if isinstance(exc, ParserError):
                raise DocumentUnprocessable(str(exc)) from exc
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


async def enqueue_rag_scan(*, document_id: uuid.UUID, ingest_attempt: int = 0) -> None:
    # F-23: the job id carries the per-document ingest attempt so a genuine tus
    # retry (attempt N->N+1) enqueues a fresh scan instead of being deduped onto a
    # retained prior result. Multipart callers keep the default 0 (that reupload
    # scan-dedup is FU-2, deferred).
    try:
        from shared_kernel.queue import enqueue

        await enqueue(
            "rag_scan_document",
            document_id=str(document_id),
            _job_id=f"rag-scan:{document_id}:{ingest_attempt}",
        )
    except Exception:
        _log.warning(
            "scan enqueue failed for rag document %s; file will not be scanned automatically",
            document_id,
            exc_info=True,
        )


__all__ = ["IngestInput", "IngestService", "MAX_MULTIPART_BYTES", "enqueue_rag_scan"]
