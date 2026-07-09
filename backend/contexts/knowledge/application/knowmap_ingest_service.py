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

import contextlib
import hashlib
import logging
import mimetypes
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.knowmap_triggers import enqueue_knowmap_build
from contexts.knowledge.application.ports import BlobStore, Embedder
from contexts.knowledge.domain.errors import (
    DocumentTooLarge,
    IngestFailed,
    KnowmapConfigNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapDocument
from contexts.knowledge.domain.models import DocumentStatus
from contexts.knowledge.infrastructure.chunkers import chunk_document
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapChunkRepository,
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.text_extraction.parsers import MIME_TO_PARSER

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
        bucket: str = "knowmap-sources",
    ) -> None:
        self._db = db
        self._blob = blob
        self._embedder = embedder
        self._bucket = bucket
        self._configs = KnowmapConfigRepository(db)
        self._docs = KnowmapDocumentRepository(db)
        self._chunks = KnowmapChunkRepository(db)

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

        mime = _normalise_mime(ipt.mime, ipt.filename)
        if mime not in MIME_TO_PARSER:
            raise UnsupportedMime(f"mime {mime!r} not in {{pdf,docx,md,txt}}")

        cfg = await self._configs.get(ipt.knowmap_config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(ipt.knowmap_config_id))

        sha = hashlib.sha256(ipt.data).hexdigest()
        existing = await self._docs.find_by_sha(knowmap_config_id=cfg.id, sha256=sha)
        if existing is not None and existing.status is DocumentStatus.READY:
            return existing
        if existing is not None:
            reindexed = await self._index_document(
                doc=existing,
                cfg=cfg,
                data=ipt.data,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            await self._db.commit()
            await enqueue_knowmap_scan(document_id=reindexed.id)
            await self._enqueue_build(cfg)
            return reindexed

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
                return existing
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
                },
                request_id=request_id,
            ),
        )
        result = await self._index_document(
            doc=doc,
            cfg=cfg,
            data=ipt.data,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        await self._db.commit()
        await enqueue_knowmap_scan(document_id=result.id)
        await self._enqueue_build(cfg)
        return result

    async def process_document(
        self,
        *,
        document_id: uuid.UUID,
        actor_ip: str | None = None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapDocument:
        """Index an already-registered document (async tus path)."""
        doc = await self._docs.get(document_id)
        if doc is None:
            raise IngestFailed(f"knowmap document {document_id} not found")
        if doc.status is DocumentStatus.READY:
            return doc
        cfg = await self._configs.get(doc.knowmap_config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(doc.knowmap_config_id))
        bucket, _, key = doc.minio_path.partition("/")
        data = await self._blob.get(bucket=bucket, key=key)
        # process_document runs in a worker; the caller commits, then the worker
        # enqueues the build so a committed corpus change is what the build reads.
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
        doc: KnowmapDocument,
        cfg: KnowmapConfig,
        data: bytes,
        actor_user_id: uuid.UUID | None,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> KnowmapDocument:
        """Parse → chunk → persist ``knowmap_chunks``, then flip status. No Qdrant:
        chunks are the build corpus, not directly-retrievable vectors."""
        try:
            text = MIME_TO_PARSER[doc.mime](data)
            pieces = await chunk_document(
                text,
                strategy=cfg.chunk_strategy,
                params=cfg.chunk_params,
                embedder=self._embedder,
            )
            # Idempotent reprocess: clear any chunks from a prior attempt so
            # re-inserting chunk_idx 0..N never collides on uq_knowmap_chunk_doc_idx.
            await self._chunks.delete_for_document(doc.id)
            if pieces:
                await self._chunks.insert_many(
                    [{"document_id": doc.id, "chunk_idx": i, "text": piece} for i, piece in enumerate(pieces)]
                )
            await self._docs.set_status(document_id=doc.id, status=DocumentStatus.READY)
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
            with contextlib.suppress(Exception):
                await self._docs.set_status(document_id=doc.id, status=DocumentStatus.FAILED)
            raise IngestFailed(f"{type(exc).__name__}: {exc}") from exc

        refreshed = await self._docs.get(doc.id)
        assert refreshed is not None
        return refreshed

    async def _enqueue_build(self, cfg: KnowmapConfig) -> None:
        await enqueue_knowmap_build(
            config_id=cfg.id,
            last_build_state=cfg.last_build_state,
            last_build_at=cfg.last_build_at,
        )


def _normalise_mime(raw: str, filename: str) -> str:
    raw = raw.split(";")[0].strip()
    if raw and raw not in {"application/octet-stream", ""}:
        return raw
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


async def enqueue_knowmap_scan(*, document_id: uuid.UUID) -> None:
    try:
        from shared_kernel.queue import enqueue

        await enqueue(
            "knowmap_scan_document",
            document_id=str(document_id),
            _job_id=f"knowmap-scan:{document_id}",
        )
    except Exception:
        _log.warning(
            "scan enqueue failed for knowmap document %s; file will not be scanned automatically",
            document_id,
            exc_info=True,
        )


__all__ = [
    "KnowmapIngestInput",
    "KnowmapIngestService",
    "MAX_MULTIPART_BYTES",
    "enqueue_knowmap_scan",
    "knowmap_source_object_key",
]
