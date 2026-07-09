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

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.knowmap_ingest_service import (
    _normalise_mime,
    enqueue_knowmap_scan,
    knowmap_source_object_key,
)
from contexts.knowledge.domain.errors import KnowmapConfigNotFound, UnsupportedMime
from contexts.knowledge.domain.knowmap import KnowmapDocument
from contexts.knowledge.domain.models import DocumentStatus
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapConfigRepository,
    KnowmapDocumentRepository,
)
from shared_kernel import audit
from shared_kernel.queue import enqueue
from shared_kernel.storage import get_minio_client
from shared_kernel.text_extraction.parsers import MIME_TO_PARSER

_SHA_BLOCK = 1024 * 1024  # 1 MiB streaming read — never loads the whole file


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_SHA_BLOCK), b""):
            h.update(block)
    return h.hexdigest()


class KnowmapTusFinalizer:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._configs = KnowmapConfigRepository(db)
        self._docs = KnowmapDocumentRepository(db)
        self._minio = get_minio_client()

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
        norm_mime = _normalise_mime(mime, filename)
        if norm_mime not in MIME_TO_PARSER:
            raise UnsupportedMime(f"mime {norm_mime!r} not in {{pdf,docx,md,txt}}")

        cfg = await self._configs.get(knowmap_config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(knowmap_config_id))

        sha = await asyncio.to_thread(_sha256_file, staging_path)
        existing = await self._docs.find_by_sha(knowmap_config_id=cfg.id, sha256=sha)
        if existing is not None and existing.status is DocumentStatus.READY:
            return existing
        if existing is not None:
            await self._enqueue_index(existing.id)
            await enqueue_knowmap_scan(document_id=existing.id)
            return existing

        key = knowmap_source_object_key(project_id=cfg.project_id, config_id=cfg.id, sha256=sha)
        await self._minio.put_file(
            bucket=self._minio.knowmap_sources_bucket,
            key=key,
            file_path=staging_path,
            content_type=norm_mime,
        )
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
                    "via": "tus",
                },
                request_id=request_id,
            ),
        )
        await self._enqueue_index(doc.id)
        await enqueue_knowmap_scan(document_id=doc.id)
        return doc

    async def _enqueue_index(self, document_id: uuid.UUID) -> None:
        # Commit the knowmap_documents row BEFORE enqueuing: the worker runs on a
        # separate connection and must see a committed row.
        await self._db.commit()
        try:
            await enqueue(
                "knowmap_ingest_document",
                document_id=str(document_id),
                _job_id=f"knowmap-ingest:{document_id}",
            )
        except Exception:
            # Arq/Redis unavailable: don't leave the committed row stuck
            # 'ingesting' with no worker. Mark it FAILED so a re-upload can retry.
            await self._docs.set_status(document_id=document_id, status=DocumentStatus.FAILED)
            await self._db.commit()
            raise


__all__ = ["KnowmapTusFinalizer"]
