"""Knowledge repositories — no cross-context joins."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.domain.errors import (
    RagConfigNameTaken,
    RagConfigNotFound,
)
from contexts.knowledge.domain.errors import (
    RagDocumentNotFound as RagDocumentNotFound,
)
from contexts.knowledge.domain.models import (
    ChunkStrategy,
    DocumentStatus,
    RagChunk,
    RagConfig,
    RagDocument,
    ScanStatus,
)
from contexts.knowledge.infrastructure import tables as t
from shared_kernel.auth.clients import now


def _row_to_config(row: Any) -> RagConfig:
    return RagConfig(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        chunk_strategy=ChunkStrategy(row.chunk_strategy),
        chunk_params=dict(row.chunk_params or {}),
        embed_key_id=row.embed_key_id,
        embed_provider=row.embed_provider,
        embed_model=row.embed_model,
        rerank_enabled=row.rerank_enabled,
        rerank_key_id=row.rerank_key_id,
        rerank_provider=row.rerank_provider,
        rerank_model=row.rerank_model,
        top_k=row.top_k,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


def _row_to_document(row: Any) -> RagDocument:
    return RagDocument(
        id=row.id,
        rag_config_id=row.rag_config_id,
        filename=row.filename,
        mime=row.mime,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        minio_path=row.minio_path,
        status=DocumentStatus(row.status),
        scan_status=ScanStatus(row.scan_status),
        scan_at=row.scan_at,
        uploaded_by=row.uploaded_by,
        uploaded_at=row.uploaded_at,
        agent_ids=tuple(row.agent_ids or ()),
    )


def _row_to_chunk(row: Any) -> RagChunk:
    return RagChunk(
        id=row.id,
        document_id=row.document_id,
        chunk_idx=row.chunk_idx,
        text=row.text,
        qdrant_point_id=row.qdrant_point_id,
    )


class RagConfigRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        chunk_strategy: ChunkStrategy,
        chunk_params: dict[str, Any],
        embed_key_id: uuid.UUID | None,
        embed_provider: str,
        embed_model: str,
        rerank_enabled: bool,
        rerank_key_id: uuid.UUID | None,
        rerank_provider: str | None,
        rerank_model: str | None,
        top_k: int,
    ) -> RagConfig:
        try:
            row = (
                await self._db.execute(
                    t.rag_configs.insert()
                    .values(
                        project_id=project_id,
                        name=name,
                        chunk_strategy=chunk_strategy.value,
                        chunk_params=chunk_params,
                        embed_key_id=embed_key_id,
                        embed_provider=embed_provider,
                        embed_model=embed_model,
                        rerank_enabled=rerank_enabled,
                        rerank_key_id=rerank_key_id,
                        rerank_provider=rerank_provider,
                        rerank_model=rerank_model,
                        top_k=top_k,
                    )
                    .returning(t.rag_configs)
                )
            ).one()
        except IntegrityError as exc:
            msg = str(exc.orig or exc).lower()
            if "uq_rag_configs_project_name_active" in msg:
                raise RagConfigNameTaken(name) from exc
            raise
        return _row_to_config(row)

    async def get(self, config_id: uuid.UUID, *, include_deleted: bool = False) -> RagConfig | None:
        predicate: sa.ColumnElement[bool] = t.rag_configs.c.id == config_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.rag_configs.c.deleted_at.is_(None))
        row = (await self._db.execute(t.rag_configs.select().where(predicate))).first()
        return _row_to_config(row) if row else None

    async def require(self, config_id: uuid.UUID) -> RagConfig:
        cfg = await self.get(config_id)
        if cfg is None:
            raise RagConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[RagConfig]:
        rows = (
            await self._db.execute(
                t.rag_configs.select()
                .where(
                    sa.and_(
                        t.rag_configs.c.project_id == project_id,
                        t.rag_configs.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.rag_configs.c.created_at.desc())
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def update(
        self,
        config_id: uuid.UUID,
        values: dict[str, Any],
    ) -> RagConfig | None:
        """Partial update of mutable fields. Returns the refreshed row."""
        if not values:
            return await self.get(config_id)
        result = await self._db.execute(
            t.rag_configs.update()
            .where(
                sa.and_(
                    t.rag_configs.c.id == config_id,
                    t.rag_configs.c.deleted_at.is_(None),
                )
            )
            .values(**values)
            .returning(t.rag_configs)
        )
        row = result.first()
        return _row_to_config(row) if row else None

    async def soft_delete(self, config_id: uuid.UUID) -> None:
        await self._db.execute(
            t.rag_configs.update().where(t.rag_configs.c.id == config_id).values(deleted_at=now())
        )


class RagDocumentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def find_by_sha(self, *, rag_config_id: uuid.UUID, sha256: str) -> RagDocument | None:
        row = (
            await self._db.execute(
                t.rag_documents.select().where(
                    sa.and_(
                        t.rag_documents.c.rag_config_id == rag_config_id,
                        t.rag_documents.c.sha256 == sha256,
                    )
                )
            )
        ).first()
        return _row_to_document(row) if row else None

    async def create(
        self,
        *,
        rag_config_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        sha256: str,
        minio_path: str,
        uploaded_by: uuid.UUID | None,
        agent_ids: Sequence[uuid.UUID] = (),
    ) -> RagDocument:
        row = (
            await self._db.execute(
                t.rag_documents.insert()
                .values(
                    rag_config_id=rag_config_id,
                    filename=filename,
                    mime=mime,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    minio_path=minio_path,
                    uploaded_by=uploaded_by,
                    agent_ids=list(agent_ids),
                )
                .returning(t.rag_documents)
            )
        ).one()
        return _row_to_document(row)

    async def set_agents(
        self,
        *,
        document_id: uuid.UUID,
        agent_ids: Sequence[uuid.UUID],
    ) -> RagDocument | None:
        """Replace a document's per-agent allowlist. Returns the refreshed row.

        Scoping is enforced in the retrieval hydration join, so this single
        Postgres write is the whole edit — no external (Qdrant) payload to keep
        in sync, hence no dual-write consistency window.
        """
        row = (
            await self._db.execute(
                t.rag_documents.update()
                .where(t.rag_documents.c.id == document_id)
                .values(agent_ids=list(agent_ids))
                .returning(t.rag_documents)
            )
        ).first()
        return _row_to_document(row) if row else None

    async def set_status(
        self,
        *,
        document_id: uuid.UUID,
        status: DocumentStatus,
    ) -> None:
        await self._db.execute(
            t.rag_documents.update().where(t.rag_documents.c.id == document_id).values(status=status.value)
        )

    async def get(self, document_id: uuid.UUID) -> RagDocument | None:
        row = (
            await self._db.execute(t.rag_documents.select().where(t.rag_documents.c.id == document_id))
        ).first()
        return _row_to_document(row) if row else None

    async def require(self, document_id: uuid.UUID) -> RagDocument:
        doc = await self.get(document_id)
        if doc is None:
            raise RagDocumentNotFound(str(document_id))
        return doc

    async def delete(self, document_id: uuid.UUID) -> None:
        """Hard-delete the document row.

        ``rag_chunks.document_id`` is ``ON DELETE CASCADE`` (see
        ``alembic/versions/0009_rag.py``), so chunks are removed atomically.
        Qdrant points and MinIO blobs are NOT touched here — that's the
        endpoint layer's job since they need infra clients we don't inject.
        """
        await self._db.execute(t.rag_documents.delete().where(t.rag_documents.c.id == document_id))

    async def mark_scan(
        self,
        *,
        document_id: uuid.UUID,
        scan_status: ScanStatus,
        scan_at: datetime,
    ) -> None:
        values: dict[str, Any] = {
            "scan_status": scan_status.value,
            "scan_at": scan_at,
        }
        if scan_status is ScanStatus.QUARANTINED:
            values["status"] = DocumentStatus.QUARANTINED.value
        await self._db.execute(
            t.rag_documents.update().where(t.rag_documents.c.id == document_id).values(**values),
        )

    async def list_for_config(
        self,
        rag_config_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[RagDocument]:
        rows = (
            await self._db.execute(
                t.rag_documents.select()
                .where(t.rag_documents.c.rag_config_id == rag_config_id)
                .order_by(t.rag_documents.c.uploaded_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_document(r) for r in rows]

    async def allowed_document_ids(
        self,
        *,
        config_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Retrievable document ids in ``config_id`` visible to ``agent_id``.

        The querying agent must be on a document's allowlist
        (``agent_ids @> [agent_id]`` — uses the GIN index from migration 0035),
        and the document must be retrievable (``ready`` and not ``quarantined``,
        matching :meth:`RagChunkRepository.lookup_points`). The retrieve path
        passes the result to Qdrant as a ``doc_id`` filter so the vector top_k is
        computed over allowed documents only.
        """
        rows = (
            await self._db.execute(
                sa.select(t.rag_documents.c.id).where(
                    t.rag_documents.c.rag_config_id == config_id,
                    t.rag_documents.c.status == "ready",
                    t.rag_documents.c.scan_status != ScanStatus.QUARANTINED.value,
                    t.rag_documents.c.agent_ids.contains([agent_id]),
                )
            )
        ).all()
        return [r.id for r in rows]

    async def get_many(self, document_ids: Sequence[uuid.UUID]) -> list[RagDocument]:
        """Batch-fetch documents by id (one round-trip) — avoids per-id N+1."""
        ids = list(document_ids)
        if not ids:
            return []
        rows = (
            await self._db.execute(t.rag_documents.select().where(t.rag_documents.c.id.in_(ids)))
        ).all()
        return [_row_to_document(r) for r in rows]


class RagChunkRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert_many(self, chunks: Sequence[dict[str, Any]]) -> None:
        if not chunks:
            return
        await self._db.execute(t.rag_chunks.insert(), list(chunks))

    async def delete_for_document(self, document_id: uuid.UUID) -> None:
        """Drop every chunk row for a document. Lets a reprocess start from a
        clean slate so re-inserting chunk_idx 0..N never collides with rows from
        a prior (failed) attempt on the ``uq_rag_chunk_doc_idx`` constraint."""
        await self._db.execute(t.rag_chunks.delete().where(t.rag_chunks.c.document_id == document_id))

    async def list_for_document(self, document_id: uuid.UUID) -> Sequence[RagChunk]:
        rows = (
            await self._db.execute(
                t.rag_chunks.select()
                .where(t.rag_chunks.c.document_id == document_id)
                .order_by(t.rag_chunks.c.chunk_idx)
            )
        ).all()
        return [_row_to_chunk(r) for r in rows]

    async def lookup_points(self, qdrant_point_ids: Sequence[uuid.UUID]) -> Sequence[RagChunk]:
        """Batch lookup — used by the retrieval path to hydrate Qdrant hits.

        Only confirmed-malicious chunks are withheld. ``scan_status == 'pending'``
        is the normal window between an ingest flipping a doc to ``ready`` and the
        async ClamAV pass landing, so excluding it would make a freshly uploaded
        doc un-retrievable until the scan finishes — an availability gap we don't
        want. We therefore drop only ``quarantined``; the ``status == 'ready'``
        guard already excludes the ingesting/failed/quarantined lifecycle states,
        and the scan_status check is defence-in-depth for a quarantine verdict
        that has not yet flipped ``status``.

        Per-agent allowlist scoping is NOT applied here — the retrieve path
        resolves the agent's visible documents up front (see
        :meth:`RagDocumentRepository.allowed_document_ids`) and passes them to
        Qdrant as a ``doc_id`` filter, so scoping happens *before* the vector
        top_k and recall is correct. This keeps a single source of truth for the
        allowlist instead of a second post-hydration filter that could drift.
        """
        if not qdrant_point_ids:
            return []
        query = (
            sa.select(t.rag_chunks)
            .join(t.rag_documents, t.rag_chunks.c.document_id == t.rag_documents.c.id)
            .where(
                t.rag_chunks.c.qdrant_point_id.in_(list(qdrant_point_ids)),
                t.rag_documents.c.status == "ready",
                t.rag_documents.c.scan_status != ScanStatus.QUARANTINED.value,
            )
        )
        rows = (await self._db.execute(query)).all()
        return [_row_to_chunk(r) for r in rows]


__all__ = [
    "RagChunkRepository",
    "RagConfigRepository",
    "RagDocumentRepository",
]
