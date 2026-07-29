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
    emit_reupload_agents_set_audit,
    emit_reupload_audit,
    rag_source_object_key,
)
from contexts.knowledge.domain.errors import (
    DocumentAllowlistConflict,
    RagConfigNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.models import DocumentStatus, RagDocument
from contexts.knowledge.domain.reupload import ReuploadAction, resolve_existing_document
from contexts.knowledge.infrastructure.channels import rag_channel
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.queue import enqueue
from shared_kernel.realtime.pubsub import Publisher
from shared_kernel.storage import get_minio_client
from shared_kernel.text_extraction.parsers import MIME_TO_PARSER, normalise_mime

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
        agent_ids: list[uuid.UUID] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> RagDocument:
        norm_mime = normalise_mime(mime, filename)
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
            submitted_agent_ids = agent_ids or []
            action = resolve_existing_document(
                status=existing.status,
                stored_agent_ids=existing.agent_ids,
                submitted_agent_ids=submitted_agent_ids,
            )
            await emit_reupload_audit(
                self._db,
                doc=existing,
                submitted_agent_ids=submitted_agent_ids,
                outcome=action,
                actor_user_id=uploaded_by,
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
                return existing

            updated = await self._docs.set_agents(
                document_id=existing.id,
                agent_ids=submitted_agent_ids,
            )
            assert updated is not None
            await emit_reupload_agents_set_audit(
                self._db,
                doc=updated,
                project_id=cfg.project_id,
                actor_user_id=uploaded_by,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            # F-23: claim_for_reingest transitions a TERMINAL row
            # (FAILED/QUARANTINED) to INGESTING and bumps ingest_attempt in ONE
            # atomic UPDATE, so only one racer wins. A returned counter means this
            # call is the genuine retry — enqueue a fresh ingest + scan whose job
            # ids carry the bumped attempt, defeating Arq's retained-id dedup. None
            # means the row was not terminal (a worker is still in flight, or a
            # concurrent re-upload already claimed it): skip the re-enqueue so two
            # workers never index the same document and collide on
            # uq_rag_chunk_doc_idx — commit the reupload audit and let it finish.
            attempt = await self._docs.claim_for_reingest(existing.id)
            if attempt is not None:
                await self._enqueue_index(existing.id, config_id=cfg.id, ingest_attempt=attempt)
                from contexts.knowledge.application.ingest_service import enqueue_rag_scan

                await enqueue_rag_scan(document_id=existing.id, ingest_attempt=attempt)
            await self._db.commit()
            return updated

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
            agent_ids=agent_ids or [],
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
                    "agent_ids": [str(agent_id) for agent_id in (agent_ids or [])],
                    "via": "tus",
                },
                request_id=request_id,
            ),
        )
        # First-time ingest: the new row carries ingest_attempt=0 (column default).
        await self._enqueue_index(doc.id, config_id=cfg.id, ingest_attempt=0)
        from contexts.knowledge.application.ingest_service import enqueue_rag_scan

        await enqueue_rag_scan(document_id=doc.id, ingest_attempt=0)
        return doc

    async def _enqueue_index(
        self, document_id: uuid.UUID, *, config_id: uuid.UUID, ingest_attempt: int
    ) -> None:
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
        # Per-attempt job id (F-23): the ingest_attempt suffix makes a genuine
        # retry (bumped attempt) a distinct id that always enqueues, while a truly
        # concurrent duplicate finalize of the *same* attempt still collapses to
        # one run — so two workers never index the same doc and collide on
        # uq_rag_chunk_doc_idx. Arq's own retry of a *failed* run reuses this id
        # (it is not a new enqueue), so transient failures still retry.
        try:
            await enqueue(
                "rag_ingest_document",
                document_id=str(document_id),
                _job_id=f"rag-ingest:{document_id}:{ingest_attempt}",
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
