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
from collections.abc import Sequence
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
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus, ScanStatus
from contexts.knowledge.infrastructure.repositories import (
    RagChunkRepository,
    RagConfigRepository,
    RagDocumentRepository,
)
from contexts.tenancy.infrastructure.repositories import ProjectRepository
from shared_kernel.auth.clients import now
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


# --------------------------------------------------------------------------- #
# 6. Retrieval hydration withholds only quarantined chunks. A ready-but-not-   #
#    yet-scanned (pending) doc stays retrievable so a fresh upload has no       #
#    availability gap; a quarantined doc is dropped.                            #
# --------------------------------------------------------------------------- #


async def _seed_ready_doc(
    db: AsyncSession,
    cfg: Any,
    user: Any,
    *,
    filename: str,
    agent_ids: Sequence[uuid.UUID] = (),
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a READY doc with one chunk; return (document_id, qdrant_point_id)."""
    sha = uuid.uuid4().hex
    doc = await RagDocumentRepository(db).create(
        rag_config_id=cfg.id,
        filename=filename,
        mime="text/plain",
        size_bytes=len(_TEXT),
        sha256=sha,
        minio_path=f"rag-sources/{cfg.project_id}/{cfg.id}/{sha}",
        uploaded_by=user.id,
        agent_ids=agent_ids,
    )
    await RagDocumentRepository(db).set_status(document_id=doc.id, status=DocumentStatus.READY)
    point_id = uuid.uuid4()
    await RagChunkRepository(db).insert_many(
        [{"document_id": doc.id, "chunk_idx": 0, "text": "body", "qdrant_point_id": point_id}]
    )
    return doc.id, point_id


async def test_lookup_points_keeps_pending_drops_quarantined() -> None:
    async with async_session() as db:
        user, cfg = await _seed_config(db)

        # Pending: ready doc whose ClamAV pass has not landed yet.
        pending_doc, pending_point = await _seed_ready_doc(db, cfg, user, filename="pending.txt")
        # Quarantined: scan flipped both scan_status and status.
        bad_doc, bad_point = await _seed_ready_doc(db, cfg, user, filename="bad.txt")
        await RagDocumentRepository(db).mark_scan(
            document_id=bad_doc, scan_status=ScanStatus.QUARANTINED, scan_at=now()
        )
        await db.commit()

        hydrated = await RagChunkRepository(db).lookup_points([pending_point, bad_point])
        returned_docs = {c.document_id for c in hydrated}

        assert pending_doc in returned_docs  # no availability gap for a fresh upload
        assert bad_doc not in returned_docs  # confirmed-malicious chunk withheld


# --------------------------------------------------------------------------- #
# 7. The per-agent allowlist is resolved by allowed_document_ids, which the     #
#    retrieve path passes to Qdrant as a doc_id filter: an agent sees a doc only #
#    if it's on the allowlist; empty allowlist = nobody; quarantined excluded.   #
# --------------------------------------------------------------------------- #


async def test_allowed_document_ids_enforces_agent_allowlist() -> None:
    async with async_session() as db:
        user, cfg = await _seed_config(db)
        agent_a, agent_b = uuid.uuid4(), uuid.uuid4()

        doc_a, _ = await _seed_ready_doc(db, cfg, user, filename="a.txt", agent_ids=[agent_a])
        doc_ab, _ = await _seed_ready_doc(db, cfg, user, filename="ab.txt", agent_ids=[agent_a, agent_b])
        doc_none, _ = await _seed_ready_doc(db, cfg, user, filename="none.txt", agent_ids=[])
        # On agent A's allowlist but quarantined — must be excluded.
        bad, _ = await _seed_ready_doc(db, cfg, user, filename="bad.txt", agent_ids=[agent_a])
        await RagDocumentRepository(db).mark_scan(
            document_id=bad, scan_status=ScanStatus.QUARANTINED, scan_at=now()
        )
        await db.commit()

        repo = RagDocumentRepository(db)
        for_a = set(await repo.allowed_document_ids(config_id=cfg.id, agent_id=agent_a))
        for_b = set(await repo.allowed_document_ids(config_id=cfg.id, agent_id=agent_b))

        assert for_a == {doc_a, doc_ab}  # excludes empty-allowlist + quarantined
        assert for_b == {doc_ab}
        assert doc_none not in for_a
        assert doc_none not in for_b
        assert bad not in for_a
