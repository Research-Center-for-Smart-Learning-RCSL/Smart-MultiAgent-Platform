"""Knowledge Map (knowmap) repositories — no cross-context joins.

Mirrors the file-RAG repositories (``repositories.py``) over the parallel
``knowmap_*`` tables. The one structural difference is the allowlist filter: file-RAG
resolves allowed *documents* and passes them to Qdrant as a chunk filter, while the
Knowledge Map resolves allowed documents so the retrieval provider can gate each
graph *relation* on all of its source documents (Q-2).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.knowledge.application.graphrag_ports import GraphRagConfigRepositoryPort
from contexts.knowledge.domain.errors import (
    KnowmapConfigNameTaken,
    KnowmapConfigNotFound,
    KnowmapDocumentNotFound,
)
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.knowmap import (
    KnowmapChunk,
    KnowmapConfig,
    KnowmapDocument,
)
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure import knowmap_tables as t
from shared_kernel.auth.clients import now


def _row_to_config(row: Any) -> KnowmapConfig:
    return KnowmapConfig(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        builder_key_group_id=row.builder_key_group_id,
        chunk_strategy=ChunkStrategy(row.chunk_strategy),
        chunk_params=dict(row.chunk_params or {}),
        embed_provider=row.embed_provider,
        embed_model=row.embed_model,
        embed_dim=row.embed_dim,
        last_build_at=row.last_build_at,
        last_build_state=BuildState(row.last_build_state),
        last_build_error=row.last_build_error,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        corpus_revision=row.corpus_revision,
        built_corpus_revision=row.built_corpus_revision,
        build_started_at=row.build_started_at,
    )


def _row_to_document(row: Any) -> KnowmapDocument:
    return KnowmapDocument(
        id=row.id,
        knowmap_config_id=row.knowmap_config_id,
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


def _row_to_chunk(row: Any) -> KnowmapChunk:
    return KnowmapChunk(
        id=row.id,
        document_id=row.document_id,
        chunk_idx=row.chunk_idx,
        text=row.text,
    )


class KnowmapConfigRepository(GraphRagConfigRepositoryPort):
    """Also satisfies the shared engine's ``GraphRagConfigRepositoryPort`` so the
    2PC builder + reconciler drive a Knowledge Map build unchanged (R11.15)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        builder_key_group_id: uuid.UUID,
        chunk_strategy: ChunkStrategy,
        chunk_params: dict[str, Any],
        embed_provider: str | None,
        embed_model: str | None,
        embed_dim: int | None,
    ) -> KnowmapConfig:
        try:
            row = (
                await self._db.execute(
                    t.knowmap_configs.insert()
                    .values(
                        project_id=project_id,
                        name=name,
                        builder_key_group_id=builder_key_group_id,
                        chunk_strategy=chunk_strategy.value,
                        chunk_params=chunk_params,
                        embed_provider=embed_provider,
                        embed_model=embed_model,
                        embed_dim=embed_dim,
                    )
                    .returning(t.knowmap_configs)
                )
            ).one()
        except IntegrityError as exc:
            msg = str(exc.orig or exc).lower()
            if "uq_knowmap_configs_project_name_active" in msg:
                raise KnowmapConfigNameTaken(name) from exc
            raise
        return _row_to_config(row)

    async def get(self, config_id: uuid.UUID, *, include_deleted: bool = False) -> KnowmapConfig | None:
        predicate: sa.ColumnElement[bool] = t.knowmap_configs.c.id == config_id
        if not include_deleted:
            predicate = sa.and_(predicate, t.knowmap_configs.c.deleted_at.is_(None))
        row = (await self._db.execute(t.knowmap_configs.select().where(predicate))).first()
        return _row_to_config(row) if row else None

    async def require(self, config_id: uuid.UUID) -> KnowmapConfig:
        cfg = await self.get(config_id)
        if cfg is None:
            raise KnowmapConfigNotFound(str(config_id))
        return cfg

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[KnowmapConfig]:
        rows = (
            await self._db.execute(
                t.knowmap_configs.select()
                .where(
                    sa.and_(
                        t.knowmap_configs.c.project_id == project_id,
                        t.knowmap_configs.c.deleted_at.is_(None),
                    )
                )
                .order_by(t.knowmap_configs.c.created_at.desc())
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def update(self, config_id: uuid.UUID, values: dict[str, Any]) -> KnowmapConfig | None:
        if not values:
            return await self.get(config_id)
        row = (
            await self._db.execute(
                t.knowmap_configs.update()
                .where(
                    sa.and_(
                        t.knowmap_configs.c.id == config_id,
                        t.knowmap_configs.c.deleted_at.is_(None),
                    )
                )
                .values(**values)
                .returning(t.knowmap_configs)
            )
        ).first()
        return _row_to_config(row) if row else None

    async def set_embed_pin(
        self,
        *,
        config_id: uuid.UUID,
        provider: str,
        model: str,
        dim: int,
    ) -> None:
        await self._db.execute(
            t.knowmap_configs.update()
            .where(t.knowmap_configs.c.id == config_id)
            .values(embed_provider=provider, embed_model=model, embed_dim=dim)
        )

    async def set_state(
        self,
        *,
        config_id: uuid.UUID,
        state: BuildState,
        error: str | None = None,
        stamp_built_at: bool = False,
        built_at: datetime | None = None,
        stamp_started_at: bool = False,
    ) -> None:
        values: dict[str, Any] = {"last_build_state": state.value, "last_build_error": error}
        # An explicit built_at (the build's started-at watermark) takes precedence;
        # stamp_built_at stamps "now" for a terminal recovery (reconciler).
        if built_at is not None:
            values["last_build_at"] = built_at
        elif stamp_built_at:
            values["last_build_at"] = now()
        # F-4: separate from the two above, which only move on a terminal outcome.
        # Set by the RUNNING transition so a stuck build has a readable age.
        if stamp_started_at:
            values["build_started_at"] = now()
        await self._db.execute(
            t.knowmap_configs.update().where(t.knowmap_configs.c.id == config_id).values(**values)
        )

    async def list_in_state(self, state: BuildState) -> Sequence[KnowmapConfig]:
        rows = (
            await self._db.execute(
                t.knowmap_configs.select().where(
                    sa.and_(
                        t.knowmap_configs.c.last_build_state == state.value,
                        t.knowmap_configs.c.deleted_at.is_(None),
                    )
                )
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_revision_divergent(
        self, *, limit: int, after_id: uuid.UUID | None = None
    ) -> Sequence[KnowmapConfig]:
        """Live IDLE configs whose committed corpus outran their last build (F-4).

        The durable statement of "a committed mutation was never built". Restricted
        to IDLE because in-flight states already have a completion path and FAILED
        is a deliberate stop (Q-2). Revision zero needs no explicit exclusion: a
        config at ``corpus_revision = 0`` cannot satisfy ``> COALESCE(built, 0)``.

        Keyset paging, not OFFSET: rows leave this result set while a sweep pages
        through it. The builder commits the RUNNING transition when a build
        *starts*, so every config a worker picks up stops matching the IDLE
        predicate, and an offset computed against the earlier, larger set would
        skip the rows that shifted past it. ``after_id`` is stable regardless.
        """
        conditions = [
            t.knowmap_configs.c.deleted_at.is_(None),
            t.knowmap_configs.c.last_build_state == BuildState.IDLE.value,
            t.knowmap_configs.c.corpus_revision
            > sa.func.coalesce(t.knowmap_configs.c.built_corpus_revision, 0),
        ]
        if after_id is not None:
            conditions.append(t.knowmap_configs.c.id > after_id)
        rows = (
            await self._db.execute(
                t.knowmap_configs.select()
                .where(sa.and_(*conditions))
                .order_by(t.knowmap_configs.c.id)
                .limit(limit)
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_stale_running(self, *, started_before: datetime, limit: int) -> Sequence[KnowmapConfig]:
        """Live configs stuck in RUNNING since before ``started_before`` (F-4).

        Observation only. Recovering a stuck RUNNING config is the reconciler's
        job -- it already carries RUNNING in its stuck-state set -- so this exists
        purely to make a reconciler that is *not* doing that job visible, rather
        than letting it strand builds silently.

        A NULL ``build_started_at`` is excluded rather than read as infinitely
        stale: a config that entered RUNNING before migration 0059 has no readable
        age, and reporting it would be a guess.
        """
        rows = (
            await self._db.execute(
                t.knowmap_configs.select()
                .where(
                    sa.and_(
                        t.knowmap_configs.c.deleted_at.is_(None),
                        t.knowmap_configs.c.last_build_state == BuildState.RUNNING.value,
                        t.knowmap_configs.c.build_started_at.is_not(None),
                        t.knowmap_configs.c.build_started_at < started_before,
                    )
                )
                .order_by(t.knowmap_configs.c.id)
                .limit(limit)
            )
        ).all()
        return [_row_to_config(r) for r in rows]

    async def list_all_ids(self, *, include_deleted: bool = False) -> set[uuid.UUID]:
        stmt = sa.select(t.knowmap_configs.c.id)
        if not include_deleted:
            stmt = stmt.where(t.knowmap_configs.c.deleted_at.is_(None))
        rows = (await self._db.execute(stmt)).all()
        return {r.id for r in rows}

    async def soft_delete(self, config_id: uuid.UUID) -> None:
        await self._db.execute(
            t.knowmap_configs.update().where(t.knowmap_configs.c.id == config_id).values(deleted_at=now())
        )

    async def bump_corpus_revision(self, config_id: uuid.UUID) -> int:
        """Atomically increment ``corpus_revision`` and return the new value (F-12).

        Called in the same transaction as a committed document mutation (add /
        reprocess / delete). The atomic SQL increment (not a read-modify-write)
        serializes concurrent mutations of the same config on the row lock, so the
        revision advances exactly once per committed change. Returns ``0`` if the
        config no longer exists (a deleted config that lost its race)."""
        row = (
            await self._db.execute(
                t.knowmap_configs.update()
                .where(t.knowmap_configs.c.id == config_id)
                .values(corpus_revision=t.knowmap_configs.c.corpus_revision + 1)
                .returning(t.knowmap_configs.c.corpus_revision)
            )
        ).first()
        return int(row.corpus_revision) if row is not None else 0

    async def set_built_corpus_revision(self, config_id: uuid.UUID, revision: int) -> None:
        """Record the corpus revision the last successful build processed (F-12)."""
        await self._db.execute(
            t.knowmap_configs.update()
            .where(t.knowmap_configs.c.id == config_id)
            .values(built_corpus_revision=revision)
        )

    async def project_pinned_dim(
        self,
        project_id: uuid.UUID,
        *,
        exclude_config_id: uuid.UUID | None = None,
    ) -> int | None:
        """The project's already-pinned knowmap embedding dimension, if any."""
        stmt = sa.select(t.knowmap_configs.c.embed_dim).where(
            sa.and_(
                t.knowmap_configs.c.project_id == project_id,
                t.knowmap_configs.c.embed_dim.isnot(None),
                t.knowmap_configs.c.deleted_at.is_(None),
            )
        )
        if exclude_config_id is not None:
            stmt = stmt.where(t.knowmap_configs.c.id != exclude_config_id)
        row = (await self._db.execute(stmt.limit(1))).first()
        return int(row.embed_dim) if row is not None else None


class KnowmapDocumentRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def find_by_sha(self, *, knowmap_config_id: uuid.UUID, sha256: str) -> KnowmapDocument | None:
        row = (
            await self._db.execute(
                t.knowmap_documents.select().where(
                    sa.and_(
                        t.knowmap_documents.c.knowmap_config_id == knowmap_config_id,
                        t.knowmap_documents.c.sha256 == sha256,
                    )
                )
            )
        ).first()
        return _row_to_document(row) if row else None

    async def create(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        sha256: str,
        minio_path: str,
        uploaded_by: uuid.UUID | None,
        agent_ids: Sequence[uuid.UUID] = (),
    ) -> KnowmapDocument:
        row = (
            await self._db.execute(
                t.knowmap_documents.insert()
                .values(
                    knowmap_config_id=knowmap_config_id,
                    filename=filename,
                    mime=mime,
                    size_bytes=size_bytes,
                    sha256=sha256,
                    minio_path=minio_path,
                    uploaded_by=uploaded_by,
                    agent_ids=list(agent_ids),
                )
                .returning(t.knowmap_documents)
            )
        ).one()
        return _row_to_document(row)

    async def set_agents(
        self,
        *,
        document_id: uuid.UUID,
        agent_ids: Sequence[uuid.UUID],
    ) -> KnowmapDocument | None:
        row = (
            await self._db.execute(
                t.knowmap_documents.update()
                .where(t.knowmap_documents.c.id == document_id)
                .values(agent_ids=list(agent_ids))
                .returning(t.knowmap_documents)
            )
        ).first()
        return _row_to_document(row) if row else None

    async def set_status(self, *, document_id: uuid.UUID, status: DocumentStatus) -> None:
        await self._db.execute(
            t.knowmap_documents.update()
            .where(t.knowmap_documents.c.id == document_id)
            .values(status=status.value)
        )

    async def claim_for_reingest(self, document_id: uuid.UUID) -> int | None:
        """Atomically claim a TERMINAL document for re-ingest (F-23).

        Mirrors :meth:`RagDocumentRepository.claim_for_reingest`: one ``UPDATE``
        transitions ``FAILED``/``QUARANTINED`` -> ``INGESTING`` and bumps
        ``ingest_attempt`` under a ``WHERE status IN (...)`` guard, ``RETURNING``
        the new counter. Exactly one concurrent re-upload wins the transition (and
        gets a counter); the losers match zero rows and get ``None`` so the
        finalizer skips their re-enqueue — no two workers collide on
        ``uq_knowmap_chunk_doc_idx``. The frozen ``KnowmapDocument`` read model does
        not carry the column — only the returned value is needed.

        Returns the new attempt counter when this call claimed the document, or
        ``None`` when it was not terminal (still ingesting, or already claimed).
        """
        row = (
            await self._db.execute(
                t.knowmap_documents.update()
                .where(
                    sa.and_(
                        t.knowmap_documents.c.id == document_id,
                        t.knowmap_documents.c.status.in_(
                            [DocumentStatus.FAILED.value, DocumentStatus.QUARANTINED.value]
                        ),
                    )
                )
                .values(
                    status=DocumentStatus.INGESTING.value,
                    ingest_attempt=t.knowmap_documents.c.ingest_attempt + 1,
                )
                .returning(t.knowmap_documents.c.ingest_attempt)
            )
        ).first()
        return int(row.ingest_attempt) if row is not None else None

    async def get(self, document_id: uuid.UUID) -> KnowmapDocument | None:
        row = (
            await self._db.execute(
                t.knowmap_documents.select().where(t.knowmap_documents.c.id == document_id)
            )
        ).first()
        return _row_to_document(row) if row else None

    async def require(self, document_id: uuid.UUID) -> KnowmapDocument:
        doc = await self.get(document_id)
        if doc is None:
            raise KnowmapDocumentNotFound(str(document_id))
        return doc

    async def delete(self, document_id: uuid.UUID) -> None:
        """Hard-delete the document row. ``knowmap_chunks.document_id`` is
        ``ON DELETE CASCADE`` so chunks follow. Qdrant points + MinIO blobs are
        purged by the endpoint layer after the DB commit (WS4)."""
        await self._db.execute(t.knowmap_documents.delete().where(t.knowmap_documents.c.id == document_id))

    async def mark_scan(
        self,
        *,
        document_id: uuid.UUID,
        scan_status: ScanStatus,
        scan_at: datetime,
    ) -> None:
        values: dict[str, Any] = {"scan_status": scan_status.value, "scan_at": scan_at}
        if scan_status is ScanStatus.QUARANTINED:
            values["status"] = DocumentStatus.QUARANTINED.value
        await self._db.execute(
            t.knowmap_documents.update().where(t.knowmap_documents.c.id == document_id).values(**values)
        )

    async def list_for_config(
        self,
        knowmap_config_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[KnowmapDocument]:
        rows = (
            await self._db.execute(
                t.knowmap_documents.select()
                .where(t.knowmap_documents.c.knowmap_config_id == knowmap_config_id)
                .order_by(t.knowmap_documents.c.uploaded_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [_row_to_document(r) for r in rows]

    async def count_locking_for_config(self, knowmap_config_id: uuid.UUID) -> int:
        """Count documents that lock the config's chunk params (F-20, Q-3).

        Mirror of :meth:`RagDocumentRepository.count_locking_for_config`: an
        ``INGESTING``/``READY`` document has consumed the config's chunk params and
        locks them; ``FAILED``/``QUARANTINED`` committed no retrievable chunks and
        do not. Keys on ``DocumentStatus``, not ``scan_status``.
        """
        row = (
            await self._db.execute(
                sa.select(sa.func.count())
                .select_from(t.knowmap_documents)
                .where(
                    t.knowmap_documents.c.knowmap_config_id == knowmap_config_id,
                    t.knowmap_documents.c.status.in_(
                        [DocumentStatus.INGESTING.value, DocumentStatus.READY.value]
                    ),
                )
            )
        ).scalar_one()
        return int(row)

    async def ready_document_ids(self, *, config_id: uuid.UUID) -> list[uuid.UUID]:
        """All build-eligible document ids in ``config_id``.

        A document is build-eligible only when it is ``ready`` **and** its scan
        verdict is ``clean`` (F-5). Requiring ``clean`` — rather than merely
        excluding ``quarantined``/``skipped`` — fails closed on a ``pending``
        document too: between commit and scan verdict a document is unindexable, so
        a never-cleanly-scanned document is never built into a Knowledge Map's
        graph even if its build races ahead of the scan. (Stricter than file-RAG,
        which admits ``pending``/``skipped`` — a deliberate choice for the
        per-agent-allowlisted Knowledge Map corpus.)

        Used by the DocDeltaLoader to scope the corpus to indexable documents."""
        rows = (
            await self._db.execute(
                sa.select(t.knowmap_documents.c.id).where(
                    t.knowmap_documents.c.knowmap_config_id == config_id,
                    t.knowmap_documents.c.status == DocumentStatus.READY.value,
                    t.knowmap_documents.c.scan_status == ScanStatus.CLEAN.value,
                )
            )
        ).all()
        return [r.id for r in rows]

    async def allowed_document_ids(
        self,
        *,
        config_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Readable document ids in ``config_id`` visible to ``agent_id`` (Q-2).

        The querying agent must be on the document's allowlist
        (``agent_ids @> [agent_id]``, GIN-indexed) and the document must be
        retrievable (``ready`` **and** ``clean`` — a document that is not cleanly
        scanned, including a still-``pending`` one, is never surfaced, matching
        :meth:`ready_document_ids`, F-5). The retrieval edge filter keeps a relation
        only if *every* one of its source documents is in this set.
        """
        rows = (
            await self._db.execute(
                sa.select(t.knowmap_documents.c.id).where(
                    t.knowmap_documents.c.knowmap_config_id == config_id,
                    t.knowmap_documents.c.status == DocumentStatus.READY.value,
                    t.knowmap_documents.c.scan_status == ScanStatus.CLEAN.value,
                    t.knowmap_documents.c.agent_ids.contains([agent_id]),
                )
            )
        ).all()
        return [r.id for r in rows]

    async def get_many(self, document_ids: Sequence[uuid.UUID]) -> list[KnowmapDocument]:
        ids = list(document_ids)
        if not ids:
            return []
        rows = (
            await self._db.execute(t.knowmap_documents.select().where(t.knowmap_documents.c.id.in_(ids)))
        ).all()
        return [_row_to_document(r) for r in rows]


class KnowmapChunkRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def insert_many(self, chunks: Sequence[dict[str, Any]]) -> None:
        if not chunks:
            return
        await self._db.execute(t.knowmap_chunks.insert(), list(chunks))

    async def delete_for_document(self, document_id: uuid.UUID) -> None:
        await self._db.execute(t.knowmap_chunks.delete().where(t.knowmap_chunks.c.document_id == document_id))

    async def list_for_document(self, document_id: uuid.UUID) -> Sequence[KnowmapChunk]:
        rows = (
            await self._db.execute(
                t.knowmap_chunks.select()
                .where(t.knowmap_chunks.c.document_id == document_id)
                .order_by(t.knowmap_chunks.c.chunk_idx)
            )
        ).all()
        return [_row_to_chunk(r) for r in rows]

    async def get_excerpts(
        self,
        refs: Sequence[tuple[uuid.UUID, int]],
        *,
        allowed_document_ids: Sequence[uuid.UUID],
    ) -> dict[tuple[uuid.UUID, int], str]:
        """Hydrate evidence excerpts by ``(document_id, chunk_idx)`` under the
        allowed-document set (WS3 security core). A ref whose document is not
        allowed is never returned, so a denied document's text can never leak."""
        allowed = set(allowed_document_ids)
        wanted = {r for r in refs if r[0] in allowed}
        if not wanted:
            return {}
        # Match exactly the requested (document_id, chunk_idx) pairs — a composite
        # tuple IN avoids the doc-IN x idx-IN cartesian over-fetch.
        rows = (
            await self._db.execute(
                sa.select(
                    t.knowmap_chunks.c.document_id,
                    t.knowmap_chunks.c.chunk_idx,
                    t.knowmap_chunks.c.text,
                ).where(
                    sa.tuple_(t.knowmap_chunks.c.document_id, t.knowmap_chunks.c.chunk_idx).in_(list(wanted))
                )
            )
        ).all()
        return {(r.document_id, r.chunk_idx): r.text for r in rows}


__all__ = [
    "KnowmapChunkRepository",
    "KnowmapConfigRepository",
    "KnowmapDocumentRepository",
]
