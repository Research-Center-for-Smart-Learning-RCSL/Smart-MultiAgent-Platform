"""Knowledge Map document ingestion (Phase 3, R11.13; AC-1/AC-6).

Reuses the shared ingestion building blocks — ``MIME_TO_PARSER`` (parse),
``chunk_document`` (chunk), MinIO SHA-addressed storage, the ``scan_status``
malware gate — over the Knowledge Map's own ``knowmap_documents`` / ``knowmap_chunks``
corpus. It is a *simplification* of :class:`IngestService`, not a fork of its rows:
knowmap chunks are the graph-build corpus and the evidence-excerpt source, so there
is no per-chunk Qdrant upsert here — only the build (WS2) embeds graph entities.

On a successful document-set change (ingest, reprocess) a ``knowmap_build`` is
enqueued (Q-3); the build reprocesses the current ``ready`` corpus.
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

from contexts.knowledge.application.ingest_ports import (
    DocumentChunker,
    KnowmapChunkIngestPort,
    KnowmapConfigIngestPort,
    KnowmapDocumentIngestPort,
)
from contexts.knowledge.application.knowmap_triggers import enqueue_knowmap_build
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
    KnowmapConfigNotFound,
    KnowmapDocumentNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapDocument
from contexts.knowledge.domain.models import DocumentStatus, IngestClaim, ScanStatus
from contexts.knowledge.domain.reupload import ReuploadAction, resolve_existing_document
from shared_kernel import audit
from shared_kernel.queue_names import KNOWLEDGE_SCAN_QUEUE
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

MAX_MULTIPART_BYTES = 32 * 1024 * 1024  # tus for anything larger


def knowmap_source_object_key(*, project_id: uuid.UUID, config_id: uuid.UUID, sha256: str) -> str:
    """Canonical MinIO key for a Knowledge Map source blob — shared by the
    multipart path and the tus finaliser (sha-addressed for idempotent re-upload)."""
    return f"{project_id}/{config_id}/{sha256}"


@dataclass(frozen=True, slots=True)
class KnowmapIngestInput:
    knowmap_config_id: uuid.UUID
    filename: str
    mime: str
    data: bytes
    uploaded_by: uuid.UUID | None
    agent_ids: tuple[uuid.UUID, ...] = ()


class KnowmapIngestService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        blob: BlobStore,
        embedder: Embedder,
        configs: KnowmapConfigIngestPort,
        documents: KnowmapDocumentIngestPort,
        chunks: KnowmapChunkIngestPort,
        chunker: DocumentChunker,
        scan_required: bool,
        bucket: str = "knowmap-sources",
    ) -> None:
        self._db = db
        self._blob = blob
        self._embedder = embedder
        self._bucket = bucket
        self._configs = configs
        self._docs = documents
        self._chunks = chunks
        self._chunker = chunker
        self._scan_required = scan_required

    async def ingest(
        self,
        *,
        ipt: KnowmapIngestInput,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapDocument:
        if len(ipt.data) > MAX_MULTIPART_BYTES:
            raise DocumentTooLarge(f"multipart upload exceeds {MAX_MULTIPART_BYTES} bytes; use tus")

        mime = normalise_mime(ipt.mime, ipt.filename)
        if mime not in MIME_TO_PARSER:
            raise UnsupportedMime(f"mime {mime!r} not in {{pdf,docx,md,txt}}")

        cfg = await self._configs.get(ipt.knowmap_config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(ipt.knowmap_config_id))

        sha = hashlib.sha256(ipt.data).hexdigest()
        existing = await self._docs.find_by_sha(knowmap_config_id=cfg.id, sha256=sha)
        if existing is not None:
            return await self._ingest_existing(
                existing=existing,
                cfg=cfg,
                ipt=ipt,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        key = knowmap_source_object_key(project_id=cfg.project_id, config_id=cfg.id, sha256=sha)
        minio_path = await self._blob.put(bucket=self._bucket, key=key, data=ipt.data, content_type=mime)

        try:
            doc = await self._docs.create(
                knowmap_config_id=cfg.id,
                filename=ipt.filename,
                mime=mime,
                size_bytes=len(ipt.data),
                sha256=sha,
                minio_path=minio_path,
                uploaded_by=ipt.uploaded_by,
                agent_ids=ipt.agent_ids,
            )
        except IntegrityError:
            await self._db.rollback()
            existing = await self._docs.find_by_sha(knowmap_config_id=cfg.id, sha256=sha)
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
                action="knowmap.document_uploaded",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="knowmap_document",
                resource_id=doc.id,
                metadata={
                    "knowmap_config_id": str(cfg.id),
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
            raise KnowmapDocumentNotFound(str(doc.id))
        # Commit the accepted upload before indexing so a parse/index failure leaves
        # a durable FAILED row (mirroring the tus path) instead of rolling the whole
        # upload back to nothing — a failed upload must stay visible in the document
        # list as FAILED, not silently vanish.
        await self._db.commit()
        if self._scan_required:
            await self._dispatch_scan(doc.id, claim)
            return doc
        await self._docs.mark_scan(
            document_id=doc.id,
            scan_status=ScanStatus.CLEAN,
            scan_at=datetime.now(UTC),
        )
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
        await self._enqueue_build_if_clean(cfg, result)
        return result

    async def _ingest_existing(
        self,
        *,
        existing: KnowmapDocument,
        cfg: KnowmapConfig,
        ipt: KnowmapIngestInput,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> KnowmapDocument:
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
                raise KnowmapDocumentNotFound(str(existing.id))
            return current

        if self._scan_required:
            await self._dispatch_scan(resolved.id, claim)
            current = await self._docs.get(resolved.id)
            if current is None:
                raise KnowmapDocumentNotFound(str(resolved.id))
            return current
        await self._docs.mark_scan(
            document_id=resolved.id,
            scan_status=ScanStatus.CLEAN,
            scan_at=datetime.now(UTC),
        )
        await self._docs.lock_for_ingest(existing.id)
        if not await self._docs.owns_claim(existing.id, claim):
            current = await self._docs.get(existing.id)
            if current is None:
                raise KnowmapDocumentNotFound(str(existing.id))
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
        await self._enqueue_build_if_clean(cfg, reindexed)
        return reindexed

    async def _dispatch_scan(self, document_id: uuid.UUID, claim: IngestClaim) -> None:
        try:
            await enqueue_knowmap_scan(
                document_id=document_id,
                ingest_attempt=claim.attempt,
                claim_token=claim.token,
            )
        except Exception as exc:
            await self._docs.finish_claim(
                document_id=document_id,
                claim=claim,
                status=DocumentStatus.FAILED,
                failure_code="ingest_failed",
            )
            await self._db.commit()
            raise IngestFailed("knowledge scan dispatch failed") from exc

    async def _resolve_existing(
        self,
        *,
        existing: KnowmapDocument,
        cfg: KnowmapConfig,
        ipt: KnowmapIngestInput,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> tuple[ReuploadAction, KnowmapDocument]:
        action = resolve_existing_document(
            status=existing.status,
            stored_agent_ids=existing.agent_ids,
            submitted_agent_ids=ipt.agent_ids,
        )
        await emit_knowmap_reupload_audit(
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
                f"use PATCH /api/knowmap-documents/{existing.id}/agents"
            )
        if action is ReuploadAction.DEDUP_NOOP:
            await self._db.commit()
            return action, existing

        updated = await self._docs.set_agents(
            document_id=existing.id,
            agent_ids=ipt.agent_ids,
        )
        if updated is None:
            raise KnowmapDocumentNotFound(str(existing.id))
        await emit_knowmap_reupload_agents_set_audit(
            self._db,
            doc=updated,
            project_id=cfg.project_id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        await self._db.commit()
        return action, updated

    async def process_document(
        self,
        *,
        document_id: uuid.UUID,
        claim: IngestClaim | None = None,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapDocument:
        """Index an already-registered document (async tus path)."""
        await self._docs.lock_for_ingest(document_id)
        doc = await self._docs.get(document_id)
        if doc is None:
            raise IngestFailed(f"knowmap document {document_id} not found")
        if claim is not None:
            if not await self._docs.owns_claim(document_id, claim):
                return doc
        elif doc.ingest_claim_token is not None:
            return doc
        if doc.status is DocumentStatus.READY:
            return doc
        if doc.scan_status is not ScanStatus.CLEAN:
            return doc
        cfg = await self._configs.get(doc.knowmap_config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(doc.knowmap_config_id))
        bucket, _, key = doc.minio_path.partition("/")
        with tempfile.TemporaryDirectory(prefix="smap-knowmap-source-") as tmpdir:
            source_path = Path(tmpdir) / "source"
            await self._blob.download_to_path(bucket=bucket, key=key, path=source_path)
            # process_document runs in a worker; the caller commits, then the worker
            # enqueues the build so a committed corpus change is what the build reads.
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
        doc: KnowmapDocument,
        cfg: KnowmapConfig,
        data: bytes | None = None,
        source_path: Path | None = None,
        actor_user_id: uuid.UUID | None,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
        claim: IngestClaim | None = None,
    ) -> KnowmapDocument:
        """Parse → chunk → persist ``knowmap_chunks``, then flip status. No Qdrant:
        chunks are the build corpus, not directly-retrievable vectors."""
        try:
            if source_path is not None:
                text = await asyncio.to_thread(parse_path, source_path, doc.mime)
            elif data is not None:
                with tempfile.TemporaryDirectory(prefix="smap-knowmap-source-") as tmpdir:
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
            # Idempotent reprocess: clear any chunks from a prior attempt so
            # re-inserting chunk_idx 0..N never collides on uq_knowmap_chunk_doc_idx.
            await self._chunks.delete_for_document(doc.id)
            if pieces:
                await self._chunks.insert_many(
                    [{"document_id": doc.id, "chunk_idx": i, "text": piece} for i, piece in enumerate(pieces)]
                )
            if claim is None:
                await self._docs.set_status(document_id=doc.id, status=DocumentStatus.READY)
            elif not await self._docs.finish_claim(
                document_id=doc.id,
                claim=claim,
                status=DocumentStatus.READY,
            ):
                raise IngestFailed(f"ingest claim for document {doc.id} is no longer current")
            # F-12: a document became part of the buildable corpus — bump the
            # monotonic corpus revision in this same transaction so the build
            # enqueued for it gets a job id distinct from any prior corpus state's.
            await self._configs.bump_corpus_revision(doc.knowmap_config_id)
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="knowmap.document_indexed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="knowmap_document",
                    resource_id=doc.id,
                    metadata={"chunks": len(pieces)},
                    request_id=request_id,
                ),
            )
        except Exception as exc:
            failure_code = (
                "resource_budget_exceeded"
                if isinstance(exc, ResourceBudgetError)
                else "document_unprocessable"
                if isinstance(exc, ParserError)
                else "ingest_failed"
            )
            # Persist FAILED durably. A new upload's row is committed before indexing
            # (see ingest()), and a reindexed document is already a committed row, so
            # the rollback here discards only the partial parse/chunk writes and the
            # FAILED status then commits onto the durable row — keeping a failed upload
            # visible as FAILED instead of vanishing from the document list. The worker
            # path re-marks FAILED in its own session either way.
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
            # A parse failure is a client-fixable input problem (unparseable, no text
            # layer, or unsupported content) → 422; any other failure (embedding,
            # provider, store) is a server-side ingest failure → 500.
            if isinstance(exc, ParserError):
                raise DocumentUnprocessable(str(exc)) from exc
            raise IngestFailed("knowledge ingestion failed") from exc

        refreshed = await self._docs.get(doc.id)
        assert refreshed is not None
        return refreshed

    async def _enqueue_build_if_clean(self, cfg: KnowmapConfig, doc: KnowmapDocument) -> None:
        """F-5: enqueue the graph build only once the document has a clean scan
        verdict. A freshly ingested document is committed ``pending``, so the build
        is deferred to the scan worker's clean-verdict path (last-writer-wins with
        the indexing-complete side). A reindex of an already-clean document (same
        content, same sha) enqueues here at once — its clean verdict still holds."""
        if doc.scan_status is not ScanStatus.CLEAN:
            return
        # F-12: target the current corpus revision (re-read fresh — the commit above
        # bumped it), so the build's dedup job id reflects the committed corpus.
        fresh = await self._configs.get(cfg.id)
        if fresh is None:
            return
        await enqueue_knowmap_build(config_id=cfg.id, target_revision=fresh.corpus_revision)


async def emit_knowmap_reupload_audit(
    db: AsyncSession,
    *,
    doc: KnowmapDocument,
    submitted_agent_ids: Sequence[uuid.UUID],
    outcome: ReuploadAction,
    actor_user_id: uuid.UUID | None,
    actor_ip: str | None,
    request_id: uuid.UUID | None,
) -> None:
    await audit.emit(
        db,
        audit.AuditEvent(
            action="knowmap.document_uploaded",
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            resource_type="knowmap_document",
            resource_id=doc.id,
            metadata={
                "knowmap_config_id": str(doc.knowmap_config_id),
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


async def emit_knowmap_reupload_agents_set_audit(
    db: AsyncSession,
    *,
    doc: KnowmapDocument,
    project_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_ip: str | None,
    request_id: uuid.UUID | None,
) -> None:
    await audit.emit(
        db,
        audit.AuditEvent(
            action="knowmap.document_agents_set",
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            resource_type="knowmap_document",
            resource_id=doc.id,
            metadata={
                "knowmap_config_id": str(doc.knowmap_config_id),
                "project_id": str(project_id),
                "agent_ids": [str(agent_id) for agent_id in doc.agent_ids],
                "source": "reupload",
            },
            request_id=request_id,
        ),
    )


async def enqueue_knowmap_scan(
    *,
    document_id: uuid.UUID,
    ingest_attempt: int,
    claim_token: uuid.UUID,
) -> None:
    # F-23: the job id carries the per-document ingest attempt so a genuine tus
    # retry enqueues a fresh scan instead of deduping onto a retained result.
    # Multipart callers keep the default 0 (that reupload scan-dedup is FU-2).
    from shared_kernel.queue import enqueue

    await enqueue(
        "knowmap_scan_document",
        document_id=str(document_id),
        ingest_attempt=ingest_attempt,
        claim_token=str(claim_token),
        _job_id=f"knowmap-scan:{document_id}:{ingest_attempt}",
        _queue_name=KNOWLEDGE_SCAN_QUEUE,
    )


__all__ = [
    "KnowmapIngestInput",
    "KnowmapIngestService",
    "MAX_MULTIPART_BYTES",
    "enqueue_knowmap_scan",
    "knowmap_source_object_key",
]
