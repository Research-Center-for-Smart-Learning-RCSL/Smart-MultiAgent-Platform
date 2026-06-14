"""Wiring tier — RAG ingestion pipeline (R10.02 / E.6).

Covers both ingestion routes against **real** Postgres + Redis, with the three
I/O ports (blob, embedder, qdrant) faked — they are external services by
design (BYO embedding provider), and the ports exist precisely so the pipeline
can be exercised without them:

  1. ``IngestService.ingest``           — synchronous multipart path (regression
                                           guard for the E.6 _index_document split).
  2. ``IngestService.process_document`` — the async tus path the
                                           ``rag_ingest_document`` worker drives:
                                           download a registered doc + index it.
  3. idempotency: re-processing a doc that already left ``ingesting`` is a no-op.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.domain.models import UserStatus
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.knowledge.application.ingest_service import (
    _EMBED_BATCH,
    IngestInput,
    IngestService,
)
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus
from contexts.knowledge.infrastructure.repositories import (
    RagConfigRepository,
    RagDocumentRepository,
)
from contexts.tenancy.infrastructure.repositories import ProjectRepository
from shared_kernel.db.session import async_session

pytestmark = pytest.mark.wiring

_TEXT = b"SMAP ingests this document. " * 50  # enough for at least one chunk


class _FakeBlob:
    """In-memory BlobStore — ``get`` returns the seeded bytes regardless of key."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.gets: list[tuple[str, str]] = []

    async def put(self, *, bucket: str, key: str, data: bytes, content_type: str) -> str:
        return f"{bucket}/{key}"

    async def get(self, *, bucket: str, key: str) -> bytes:
        self.gets.append((bucket, key))
        return self._data


class _FakeEmbedder:
    vector_size = 8

    def __init__(self) -> None:
        self.calls = 0
        self.max_batch = 0
        self.total = 0

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.max_batch = max(self.max_batch, len(texts))
        self.total += len(texts)
        return [[0.1] * self.vector_size for _ in texts]


class _FakeQdrant:
    def __init__(self) -> None:
        self.upserts: list[Any] = []
        self.deletes: list[uuid.UUID] = []

    async def ensure_collection(self, project_id: uuid.UUID, *, vector_size: int) -> None:
        return None

    async def upsert_chunks(self, *, project_id: uuid.UUID, points: Any) -> None:
        self.upserts.append(list(points))

    async def delete_document(self, *, project_id: uuid.UUID, document_id: uuid.UUID) -> None:
        self.deletes.append(document_id)


async def _seed_config(db: AsyncSession, *, chunk_params: dict[str, Any] | None = None) -> tuple[Any, Any]:
    u = uuid.uuid4().hex[:8]
    user = await UserRepository(db).insert(
        email=f"rag-{u}@example.com", password_hash="x" * 16, status=UserStatus.ACTIVE
    )
    project = await ProjectRepository(db).create(
        name=f"rp-{u}", owner_user_id=user.id, owner_org_id=None, created_by_user_id=user.id
    )
    cfg = await RagConfigRepository(db).create(
        project_id=project.id,
        name=f"rag-{u}",
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params=chunk_params or {},
        embed_key_id=None,
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=False,
        rerank_key_id=None,
        rerank_provider=None,
        rerank_model=None,
        top_k=5,
    )
    return user, cfg


def _ingest_service(
    db: AsyncSession,
    blob: _FakeBlob,
    embedder: _FakeEmbedder | None = None,
    qdrant: _FakeQdrant | None = None,
) -> IngestService:
    return IngestService(
        db,
        blob=blob,  # type: ignore[arg-type]
        embedder=embedder or _FakeEmbedder(),  # type: ignore[arg-type]
        qdrant=qdrant or _FakeQdrant(),  # type: ignore[arg-type]
        bucket="rag-sources",
    )


async def _chunk_count(db: AsyncSession, document_id: uuid.UUID) -> int:
    return (
        await db.execute(
            sa.text("SELECT count(*) FROM rag_chunks WHERE document_id = :d"),
            {"d": document_id},
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
# 1. Multipart ingest still indexes after the _index_document extraction.      #
# --------------------------------------------------------------------------- #


async def test_multipart_ingest_indexes_document() -> None:
    async with async_session() as db:
        user, cfg = await _seed_config(db)
        await db.commit()

        ingest = _ingest_service(db, _FakeBlob(_TEXT))
        doc = await ingest.ingest(
            ipt=IngestInput(
                rag_config_id=cfg.id,
                filename="doc.txt",
                mime="text/plain",
                data=_TEXT,
                uploaded_by=user.id,
            ),
            actor_user_id=user.id,
            actor_ip=None,
        )
        await db.commit()

        assert doc.status is DocumentStatus.READY
        assert await _chunk_count(db, doc.id) >= 1


# --------------------------------------------------------------------------- #
# 2. process_document downloads a registered doc and indexes it (E.6 worker).  #
# --------------------------------------------------------------------------- #


async def test_process_document_indexes_registered_doc() -> None:
    async with async_session() as db:
        user, cfg = await _seed_config(db)
        # Register a doc the way RagTusFinalizer does: an 'ingesting' row whose
        # minio_path points at the (faked) blob.
        sha = uuid.uuid4().hex
        doc = await RagDocumentRepository(db).create(
            rag_config_id=cfg.id,
            filename="big.txt",
            mime="text/plain",
            size_bytes=len(_TEXT),
            sha256=sha,
            minio_path=f"rag-sources/{cfg.project_id}/{cfg.id}/{sha}",
            uploaded_by=user.id,
        )
        await db.commit()
        assert doc.status is DocumentStatus.INGESTING

        blob = _FakeBlob(_TEXT)
        result = await _ingest_service(db, blob).process_document(document_id=doc.id)
        await db.commit()

        assert result.status is DocumentStatus.READY
        assert await _chunk_count(db, doc.id) >= 1
        # It downloaded from the path stored on the row.
        assert blob.gets == [("rag-sources", f"{cfg.project_id}/{cfg.id}/{sha}")]


# --------------------------------------------------------------------------- #
# 3. Re-processing a doc that already left 'ingesting' is a no-op.             #
# --------------------------------------------------------------------------- #


async def test_process_document_idempotent_when_already_ready() -> None:
    async with async_session() as db:
        user, cfg = await _seed_config(db)
        sha = uuid.uuid4().hex
        doc = await RagDocumentRepository(db).create(
            rag_config_id=cfg.id,
            filename="done.txt",
            mime="text/plain",
            size_bytes=len(_TEXT),
            sha256=sha,
            minio_path=f"rag-sources/{cfg.project_id}/{cfg.id}/{sha}",
            uploaded_by=user.id,
        )
        await RagDocumentRepository(db).set_status(document_id=doc.id, status=DocumentStatus.READY)
        await db.commit()

        blob = _FakeBlob(_TEXT)
        result = await _ingest_service(db, blob).process_document(document_id=doc.id)

        assert result.status is DocumentStatus.READY
        # Early return — the blob was never downloaded, no chunks written.
        assert blob.gets == []
        assert await _chunk_count(db, doc.id) == 0


# --------------------------------------------------------------------------- #
# 4. A FAILED doc is reprocessed (Arq retry / re-upload recovery), not skipped.#
# --------------------------------------------------------------------------- #


async def test_process_document_reprocesses_failed_doc() -> None:
    async with async_session() as db:
        user, cfg = await _seed_config(db)
        sha = uuid.uuid4().hex
        doc = await RagDocumentRepository(db).create(
            rag_config_id=cfg.id,
            filename="retry.txt",
            mime="text/plain",
            size_bytes=len(_TEXT),
            sha256=sha,
            minio_path=f"rag-sources/{cfg.project_id}/{cfg.id}/{sha}",
            uploaded_by=user.id,
        )
        await RagDocumentRepository(db).set_status(document_id=doc.id, status=DocumentStatus.FAILED)
        await db.commit()

        blob = _FakeBlob(_TEXT)
        result = await _ingest_service(db, blob).process_document(document_id=doc.id)
        await db.commit()

        # Reprocessed, not skipped: blob downloaded, status recovered to READY.
        assert result.status is DocumentStatus.READY
        assert blob.gets != []
        assert await _chunk_count(db, doc.id) >= 1


# --------------------------------------------------------------------------- #
# 5. Embedding is batched (≤ _EMBED_BATCH per call) so a huge doc can't 413/OOM.#
# --------------------------------------------------------------------------- #


async def test_index_batches_embeddings_for_large_documents() -> None:
    async with async_session() as db:
        # Tiny chunk size forces many chunks from modest text → multiple batches.
        user, cfg = await _seed_config(db, chunk_params={"chunk_size_tokens": 4, "chunk_overlap_tokens": 0})
        big_text = ("word " * 4000).encode()
        embedder = _FakeEmbedder()
        doc = await _ingest_service(db, _FakeBlob(big_text), embedder=embedder).ingest(
            ipt=IngestInput(
                rag_config_id=cfg.id,
                filename="big.txt",
                mime="text/plain",
                data=big_text,
                uploaded_by=user.id,
            ),
            actor_user_id=user.id,
            actor_ip=None,
        )
        await db.commit()

        assert doc.status is DocumentStatus.READY
        total_chunks = await _chunk_count(db, doc.id)
        assert total_chunks > _EMBED_BATCH  # enough to require >1 batch
        assert embedder.calls >= 2  # batching actually engaged
        assert embedder.max_batch <= _EMBED_BATCH  # no oversized provider call
        assert embedder.total == total_chunks  # every chunk embedded exactly once
