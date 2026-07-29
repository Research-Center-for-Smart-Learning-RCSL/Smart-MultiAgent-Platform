"""Finalise a completed tus ``purpose=knowmap_source`` upload (Phase 3).

The *register* half of large-file Knowledge Map ingestion: the tus PATCH layer has
filled a staging file on disk; we stream it into the ``knowmap-sources`` bucket,
create the ``knowmap_documents`` row in ``ingesting`` state, and enqueue the
``knowmap_ingest_document`` Arq worker (parse/chunk/persist off the request path)
plus the malware scan. Mirrors :class:`RagTusFinalizer`; the byte layout matches the
synchronous multipart path (``knowmap-sources/{project_id}/{config_id}/{sha256}``).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.ingest_ports import (
    KnowmapConfigIngestPort,
    KnowmapDocumentIngestPort,
    StagedSourcePort,
)
from contexts.knowledge.application.knowmap_ingest_service import (
    emit_knowmap_reupload_agents_set_audit,
    emit_knowmap_reupload_audit,
    enqueue_knowmap_scan,
    knowmap_source_object_key,
)
from contexts.knowledge.domain.errors import (
    DocumentAllowlistConflict,
    KnowmapConfigNotFound,
    KnowmapDocumentNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.knowmap import KnowmapDocument
from contexts.knowledge.domain.models import DocumentStatus, IngestClaim
from contexts.knowledge.domain.reupload import ReuploadAction, resolve_existing_document
from shared_kernel import audit
from shared_kernel.queue import enqueue
from shared_kernel.text_extraction.parsers import MIME_TO_PARSER, normalise_mime

_SHA_BLOCK = 1024 * 1024  # 1 MiB streaming read — never loads the whole file


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_SHA_BLOCK), b""):
            h.update(block)
    return h.hexdigest()


class KnowmapTusFinalizer:
    def __init__(
        self,
        db: AsyncSession,
        *,
        configs: KnowmapConfigIngestPort,
        documents: KnowmapDocumentIngestPort,
        staged_source: StagedSourcePort,
    ) -> None:
        self._db = db
        self._configs = configs
        self._docs = documents
        self._minio = staged_source

    async def finalize(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        filename: str,
        mime: str,
        staging_path: str,
        size_bytes: int,
        uploaded_by: uuid.UUID | None,
        actor_ip: str | None,
        agent_ids: list[uuid.UUID] | None = None,
        request_id: uuid.UUID | None = None,
    ) -> KnowmapDocument:
        norm_mime = normalise_mime(mime, filename)
        if norm_mime not in MIME_TO_PARSER:
            raise UnsupportedMime(f"mime {norm_mime!r} not in {{pdf,docx,md,txt}}")

        cfg = await self._configs.get(knowmap_config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(knowmap_config_id))

        sha = await asyncio.to_thread(_sha256_file, staging_path)
        existing = await self._docs.find_by_sha(knowmap_config_id=cfg.id, sha256=sha)
        if existing is not None:
            submitted_agent_ids = agent_ids or []
            action = resolve_existing_document(
                status=existing.status,
                stored_agent_ids=existing.agent_ids,
                submitted_agent_ids=submitted_agent_ids,
            )
            await emit_knowmap_reupload_audit(
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
                    f"use PATCH /api/knowmap-documents/{existing.id}/agents"
                )
            if action is ReuploadAction.DEDUP_NOOP:
                await self._db.commit()
                return existing

            updated = await self._docs.set_agents(
                document_id=existing.id,
                agent_ids=submitted_agent_ids,
            )
            if updated is None:
                raise KnowmapDocumentNotFound(str(existing.id))
            await emit_knowmap_reupload_agents_set_audit(
                self._db,
                doc=updated,
                project_id=cfg.project_id,
                actor_user_id=uploaded_by,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            # F-23: claim_for_reingest transitions a TERMINAL row
            # (FAILED/QUARANTINED) to INGESTING and bumps ingest_attempt in ONE
            # atomic UPDATE. A returned counter means this call is the genuine retry
            # — enqueue a fresh ingest + scan whose job ids carry the bumped attempt
            # so Arq does not dedup onto the retained prior result. None means the
            # row was not terminal (a worker is still in flight, or a concurrent
            # re-upload already claimed it): skip the re-enqueue so two workers never
            # index the same doc and collide on uq_knowmap_chunk_doc_idx.
            claim = await self._docs.claim_for_reingest(existing.id)
            if claim is not None:
                await self._enqueue_index(
                    existing.id,
                    ingest_attempt=claim.attempt,
                    claim_token=claim.token,
                )
                await enqueue_knowmap_scan(document_id=existing.id, ingest_attempt=claim.attempt)
            await self._db.commit()
            return updated

        key = knowmap_source_object_key(project_id=cfg.project_id, config_id=cfg.id, sha256=sha)
        await self._minio.put_file(
            bucket=self._minio.knowmap_sources_bucket,
            key=key,
            file_path=staging_path,
            content_type=norm_mime,
        )
        try:
            doc = await self._docs.create(
                knowmap_config_id=cfg.id,
                filename=filename,
                mime=norm_mime,
                size_bytes=size_bytes,
                sha256=sha,
                minio_path=f"{self._minio.knowmap_sources_bucket}/{key}",
                uploaded_by=uploaded_by,
                agent_ids=agent_ids or [],
            )
        except IntegrityError:
            await self._db.rollback()
            return await self.finalize(
                knowmap_config_id=knowmap_config_id,
                filename=filename,
                mime=mime,
                staging_path=staging_path,
                size_bytes=size_bytes,
                uploaded_by=uploaded_by,
                actor_ip=actor_ip,
                agent_ids=agent_ids,
                request_id=request_id,
            )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="knowmap.document_uploaded",
                actor_user_id=uploaded_by,
                actor_ip=actor_ip,
                resource_type="knowmap_document",
                resource_id=doc.id,
                metadata={
                    "knowmap_config_id": str(cfg.id),
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
        claim = await self._docs.claim_initial(doc.id)
        if claim is None:
            raise KnowmapDocumentNotFound(str(doc.id))
        await self._enqueue_index(
            doc.id,
            ingest_attempt=claim.attempt,
            claim_token=claim.token,
        )
        await enqueue_knowmap_scan(document_id=doc.id, ingest_attempt=0)
        return doc

    async def _enqueue_index(
        self,
        document_id: uuid.UUID,
        *,
        ingest_attempt: int,
        claim_token: uuid.UUID,
    ) -> None:
        # Commit the knowmap_documents row BEFORE enqueuing: the worker runs on a
        # separate connection and must see a committed row.
        await self._db.commit()
        try:
            await enqueue(
                "knowmap_ingest_document",
                document_id=str(document_id),
                ingest_attempt=ingest_attempt,
                claim_token=str(claim_token),
                # Per-attempt job id (F-23): a bumped attempt is a distinct id that
                # always enqueues; a concurrent duplicate of the same attempt dedups.
                _job_id=f"knowmap-ingest:{document_id}:{ingest_attempt}",
            )
        except Exception:
            # Arq/Redis unavailable: don't leave the committed row stuck
            # 'ingesting' with no worker. Mark it FAILED so a re-upload can retry.
            await self._docs.finish_claim(
                document_id=document_id,
                claim=IngestClaim(
                    attempt=ingest_attempt,
                    token=claim_token,
                    until=datetime.max.replace(tzinfo=UTC),
                ),
                status=DocumentStatus.FAILED,
            )
            await self._db.commit()
            raise


__all__ = ["KnowmapTusFinalizer"]
