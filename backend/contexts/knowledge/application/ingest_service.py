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

import asyncio
import contextlib
import hashlib
import logging
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.channels import rag_channel
from contexts.knowledge.application.ingest_ports import (
    DocumentChunker,
    RagChunkIngestPort,
    RagConfigIngestPort,
    RagDocumentIngestPort,
    RagVectorIngestPort,
)
from contexts.knowledge.application.ports import BlobStore, Embedder
from contexts.knowledge.application.resource_budgets import (
    MAX_DOCUMENT_CHUNKS,
    enforce_chunk_budget,
)
from contexts.knowledge.domain.errors import (
    DocumentAllowlistConflict,
    DocumentTooLarge,
    DocumentUnprocessable,
    IngestFailed,
    RagConfigNotFound,
    RagDocumentNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.models import (
    DocumentStatus,
    IngestClaim,
    RagConfig,
    RagDocument,
    ScanStatus,
)
from contexts.knowledge.domain.reupload import ReuploadAction, resolve_existing_document
from shared_kernel import audit
from shared_kernel.queue_names import KNOWLEDGE_SCAN_QUEUE
from shared_kernel.realtime.pubsub import Publisher
from shared_kernel.text_extraction.parsers import (
    MIME_TO_PARSER,
    ParserError,
    ResourceBudgetError,
    normalise_mime,
)
from shared_kernel.text_extraction.parsers import (
    parse_path_isolated as parse_path,
)

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


async def _publish_ingestion_started(*, config_id: uuid.UUID, document_id: uuid.UUID) -> None:
    try:
        await Publisher(rag_channel(config_id)).emit(
            "ingestion.started",
            {"document_id": str(document_id), "total": 1},
        )
    except Exception:
        _log.debug(
            "ingestion-start publish failed for document %s",
            document_id,
            exc_info=True,
        )


async def _emit_best_effort(
    publisher: Publisher,
    event: str,
    payload: dict[str, object],
    *,
    document_id: uuid.UUID,
) -> None:
    try:
        await publisher.emit(event, payload)
    except Exception:
        _log.warning(
            "failed to publish %s for document %s",
            event,
            document_id,
            exc_info=True,
        )


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
        qdrant: RagVectorIngestPort,
        configs: RagConfigIngestPort,
        documents: RagDocumentIngestPort,
        chunks: RagChunkIngestPort,
        chunker: DocumentChunker,
        scan_required: bool,
        bucket: str = "rag-sources",
    ) -> None:
        self._db = db
        self._blob = blob
        self._embedder = embedder
        self._qdrant = qdrant
        self._bucket = bucket
        self._configs = configs
        self._docs = documents
        self._chunks = chunks
        self._chunker = chunker
        self._scan_required = scan_required

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
        if existing is not None:
            return await self._ingest_existing(
                existing=existing,
                cfg=cfg,
                ipt=ipt,
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
                _, resolved = await self._resolve_existing(
                    existing=existing,
                    cfg=cfg,
                    ipt=ipt,
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    request_id=request_id,
                )
                return resolved
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
                    "agent_ids": [str(agent_id) for agent_id in ipt.agent_ids],
                },
                request_id=request_id,
            ),
        )
        claim = await self._docs.claim_initial(doc.id)
        if claim is None:
            raise RagDocumentNotFound(str(doc.id))
        # Live status for clients watching ws:rag:{config_id} (useRagConfigSocket).
        # Multipart ingest is synchronous (one doc per request), so we emit the
        # start/terminal events only — there is no incremental progress to report.
        # Fire-and-forget: the frontend refetches authoritative state on receipt.
        await _publish_ingestion_started(config_id=cfg.id, document_id=doc.id)

        # Commit the accepted upload before indexing so an index failure leaves a
        # durable FAILED row (see _index_document) instead of rolling the whole
        # upload back to nothing — a failed upload must stay visible in the list.
        await self._db.commit()
        if self._scan_required:
            await self._dispatch_scan(doc.id, cfg.id, claim)
            return doc
        await self._docs.mark_scan(
            document_id=doc.id,
            scan_status=ScanStatus.CLEAN,
            scan_at=datetime.now(UTC),
        )
        # Commit the verdict before indexing. _index_document's failure handler
        # rolls back, so an uncommitted CLEAN would be discarded and the document
        # would settle as FAILED with scan_status still `pending` -- which the
        # re-ingest guard reads as "never scanned" and refuses to index, leaving
        # no way to recover it. The commit must also precede lock_for_ingest,
        # whose advisory lock is transaction-scoped.
        await self._db.commit()
        await self._docs.lock_for_ingest(doc.id)
        if not await self._docs.owns_claim(doc.id, claim):
            return await self._docs.require(doc.id)
        result = await self._index_document(
            doc=doc,
            cfg=cfg,
            data=ipt.data,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
            claim=claim,
        )
        await self._db.commit()
        return result

    async def _ingest_existing(
        self,
        *,
        existing: RagDocument,
        cfg: RagConfig,
        ipt: IngestInput,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> RagDocument:
        action, resolved = await self._resolve_existing(
            existing=existing,
            cfg=cfg,
            ipt=ipt,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        if action is ReuploadAction.DEDUP_NOOP:
            return resolved
        if existing.status is DocumentStatus.INGESTING:
            return resolved
        claim = await self._docs.claim_for_reingest(existing.id)
        await self._db.commit()
        if claim is None:
            current = await self._docs.get(existing.id)
            if current is None:
                raise RagDocumentNotFound(str(existing.id))
            return current

        await _publish_ingestion_started(config_id=cfg.id, document_id=resolved.id)
        if self._scan_required:
            await self._dispatch_scan(resolved.id, cfg.id, claim)
            current = await self._docs.get(resolved.id)
            if current is None:
                raise RagDocumentNotFound(str(resolved.id))
            return current
        await self._docs.mark_scan(
            document_id=resolved.id,
            scan_status=ScanStatus.CLEAN,
            scan_at=datetime.now(UTC),
        )
        # See the sibling commit in `ingest`: an uncommitted CLEAN is discarded by
        # _index_document's rollback, stranding the document as FAILED/pending.
        await self._db.commit()
        await self._docs.lock_for_ingest(existing.id)
        if not await self._docs.owns_claim(existing.id, claim):
            current = await self._docs.get(existing.id)
            if current is None:
                raise RagDocumentNotFound(str(existing.id))
            return current
        reindexed = await self._index_document(
            doc=resolved,
            cfg=cfg,
            data=ipt.data,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
            claim=claim,
        )
        await self._db.commit()
        return reindexed

    async def _dispatch_scan(
        self,
        document_id: uuid.UUID,
        config_id: uuid.UUID,
        claim: IngestClaim,
    ) -> None:
        try:
            await enqueue_rag_scan(
                document_id=document_id,
                ingest_attempt=claim.attempt,
                claim_token=claim.token,
            )
        except Exception as exc:
            finished = await self._docs.finish_claim(
                document_id=document_id,
                claim=claim,
                status=DocumentStatus.FAILED,
                failure_code="ingest_failed",
            )
            await self._db.commit()
            if finished:
                await _emit_best_effort(
                    Publisher(rag_channel(config_id)),
                    "ingestion.failed",
                    {
                        "document_id": str(document_id),
                        "error": "could not enqueue scan job",
                    },
                    document_id=document_id,
                )
            raise IngestFailed("knowledge scan dispatch failed") from exc

    async def _resolve_existing(
        self,
        *,
        existing: RagDocument,
        cfg: RagConfig,
        ipt: IngestInput,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> tuple[ReuploadAction, RagDocument]:
        action = resolve_existing_document(
            status=existing.status,
            stored_agent_ids=existing.agent_ids,
            submitted_agent_ids=ipt.agent_ids,
        )
        await emit_reupload_audit(
            self._db,
            doc=existing,
            submitted_agent_ids=ipt.agent_ids,
            outcome=action,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        if action is ReuploadAction.CONFLICT:
            await self._db.commit()
            raise DocumentAllowlistConflict(
                f"document {existing.id} already exists with a different agent allowlist; "
                f"use PATCH /api/rag-documents/{existing.id}/agents"
            )
        if action is ReuploadAction.DEDUP_NOOP:
            await self._db.commit()
            return action, existing

        updated = await self._docs.set_agents(
            document_id=existing.id,
            agent_ids=ipt.agent_ids,
        )
        if updated is None:
            raise RagDocumentNotFound(str(existing.id))
        await emit_reupload_agents_set_audit(
            self._db,
            doc=updated,
            project_id=cfg.project_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        # The index failure path rolls back partial writes, so the new allowlist
        # and retry audit must be durable before indexing begins.
        await self._db.commit()
        return action, updated

    async def process_document(
        self,
        *,
        document_id: uuid.UUID,
        claim: IngestClaim | None = None,
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
        await self._docs.lock_for_ingest(document_id)
        doc = await self._docs.get(document_id)
        if doc is None:
            raise IngestFailed(f"document {document_id} not found")
        if claim is not None:
            if not await self._docs.owns_claim(document_id, claim):
                return doc
        elif doc.ingest_claim_token is not None:
            return doc
        if doc.status is DocumentStatus.READY:
            # Already indexed — idempotent no-op (a duplicate enqueue or a retry
            # after success). A FAILED/INGESTING doc is (re)processed so an Arq
            # retry of a transient failure actually re-indexes.
            return doc
        if doc.scan_status is not ScanStatus.CLEAN:
            return doc
        cfg = await self._configs.get(doc.rag_config_id)
        if cfg is None:
            raise RagConfigNotFound(str(doc.rag_config_id))

        bucket, _, key = doc.minio_path.partition("/")
        with tempfile.TemporaryDirectory(prefix="smap-rag-source-") as tmpdir:
            source_path = Path(tmpdir) / "source"
            await self._blob.download_to_path(bucket=bucket, key=key, path=source_path)
            return await self._index_document(
                doc=doc,
                cfg=cfg,
                source_path=source_path,
                actor_user_id=doc.uploaded_by,
                actor_ip=actor_ip,
                request_id=request_id,
                claim=claim,
            )

    async def _index_document(
        self,
        *,
        doc: RagDocument,
        cfg: RagConfig,
        data: bytes | None = None,
        source_path: Path | None = None,
        actor_user_id: uuid.UUID | None,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
        claim: IngestClaim | None = None,
    ) -> RagDocument:
        """Parse → chunk → embed → upsert for a registered document, then flip
        status + emit the terminal ws event. Shared by the synchronous
        multipart ``ingest`` path and the async ``process_document`` worker path
        so both index identically. The caller owns registration + the
        ``ingestion.started`` event."""
        try:
            if source_path is not None:
                text = await asyncio.to_thread(parse_path, source_path, doc.mime)
            elif data is not None:
                with tempfile.TemporaryDirectory(prefix="smap-rag-source-") as tmpdir:
                    source_path = Path(tmpdir) / "source"
                    await asyncio.to_thread(source_path.write_bytes, data)
                    text = await asyncio.to_thread(parse_path, source_path, doc.mime)
            else:
                raise ValueError("data or source_path is required")
            pieces = await self._chunker(
                text,
                strategy=cfg.chunk_strategy,
                params=cfg.chunk_params,
                embedder=self._embedder,
                max_chunks=MAX_DOCUMENT_CHUNKS,
            )
            enforce_chunk_budget(pieces)
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
                    await _emit_best_effort(
                        pub,
                        "ingestion.progress",
                        {
                            "document_id": str(doc.id),
                            "processed": processed,
                            "total": total_chunks,
                        },
                        document_id=doc.id,
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
                    await _emit_best_effort(
                        pub,
                        "ingestion.indexing",
                        {"document_id": str(doc.id), "batch_start": start},
                        document_id=doc.id,
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
            if claim is None:
                await self._docs.set_status(
                    document_id=doc.id,
                    status=DocumentStatus.READY,
                )
            elif not await self._docs.finish_claim(
                document_id=doc.id,
                claim=claim,
                status=DocumentStatus.READY,
            ):
                raise IngestFailed(f"ingest claim for document {doc.id} is no longer current")
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
            await _emit_best_effort(
                Publisher(rag_channel(cfg.id)),
                "ingestion.completed",
                {"document_id": str(doc.id), "chunks": len(pieces)},
                document_id=doc.id,
            )
        except Exception as exc:  # — any failure → mark + surface
            failure_code = (
                "resource_budget_exceeded"
                if isinstance(exc, ResourceBudgetError)
                else "document_unprocessable"
                if isinstance(exc, ParserError)
                else "ingest_failed"
            )
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
                if claim is None:
                    await self._docs.set_status(
                        document_id=doc.id,
                        status=DocumentStatus.FAILED,
                        failure_code=failure_code,
                    )
                else:
                    await self._docs.finish_claim(
                        document_id=doc.id,
                        claim=claim,
                        status=DocumentStatus.FAILED,
                        failure_code=failure_code,
                    )
                await self._db.commit()
            with contextlib.suppress(Exception):
                await Publisher(rag_channel(cfg.id)).emit(
                    "ingestion.failed",
                    {"document_id": str(doc.id), "failure_code": failure_code},
                )
            # A parse failure is a client-fixable input problem (unparseable, no text
            # layer, or unsupported content) → 422; any other failure (embedding,
            # provider, store) is a server-side ingest failure → 500.
            if isinstance(exc, ParserError):
                raise DocumentUnprocessable(str(exc)) from exc
            raise IngestFailed("knowledge ingestion failed") from exc

        # Re-read so the returned row reflects the just-set status (the caller
        # owns the commit).
        refreshed = await self._docs.get(doc.id)
        assert refreshed is not None
        return refreshed


async def emit_reupload_audit(
    db: AsyncSession,
    *,
    doc: RagDocument,
    submitted_agent_ids: Sequence[uuid.UUID],
    outcome: ReuploadAction,
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
                "agent_ids": [str(agent_id) for agent_id in submitted_agent_ids],
                "reupload": True,
                "outcome": outcome.value,
            },
            request_id=request_id,
        ),
    )


async def emit_reupload_agents_set_audit(
    db: AsyncSession,
    *,
    doc: RagDocument,
    project_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_ip: str | None,
    request_id: uuid.UUID | None,
) -> None:
    await audit.emit(
        db,
        audit.AuditEvent(
            action="rag.document_agents_set",
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            resource_type="rag_document",
            resource_id=doc.id,
            metadata={
                "rag_config_id": str(doc.rag_config_id),
                "project_id": str(project_id),
                "agent_ids": [str(agent_id) for agent_id in doc.agent_ids],
                "source": "reupload",
            },
            request_id=request_id,
        ),
    )


async def enqueue_rag_scan(
    *,
    document_id: uuid.UUID,
    ingest_attempt: int,
    claim_token: uuid.UUID,
) -> None:
    # F-23: the job id carries the per-document ingest attempt so a genuine tus
    # retry (attempt N->N+1) enqueues a fresh scan instead of being deduped onto a
    # retained prior result. Multipart callers keep the default 0 (that reupload
    # scan-dedup is FU-2, deferred).
    from shared_kernel.queue import enqueue

    await enqueue(
        "rag_scan_document",
        document_id=str(document_id),
        ingest_attempt=ingest_attempt,
        claim_token=str(claim_token),
        _job_id=f"rag-scan:{document_id}:{ingest_attempt}",
        _queue_name=KNOWLEDGE_SCAN_QUEUE,
    )


__all__ = ["MAX_MULTIPART_BYTES", "IngestInput", "IngestService", "enqueue_rag_scan"]
