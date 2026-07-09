"""Unit tests for KnowmapIngestService (Phase 3, R11.13 / AC-1 / AC-6).

The Knowledge Map ingest reuses the shared ingestion building blocks (MIME parse,
``chunk_document``, MinIO SHA-addressed storage, the scan gate) over its own
``knowmap_documents`` / ``knowmap_chunks`` corpus — with **no** per-chunk Qdrant
upsert (chunks are the graph-build corpus, not directly-retrievable vectors). A
successful document-set change enqueues a ``knowmap_build`` (never a conversation
trigger). All infrastructure is mocked via the Protocol ports — no real I/O.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.knowledge.application.knowmap_ingest_service import (
    KnowmapIngestInput,
    KnowmapIngestService,
)
from contexts.knowledge.domain.errors import IngestFailed, KnowmapConfigNotFound
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapDocument
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus, ScanStatus

_MOD = "contexts.knowledge.application.knowmap_ingest_service"
_NOW = datetime(2026, 7, 7, 12, 0, 0)
_PROJECT_ID = uuid.uuid4()
_CONFIG_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _make_config() -> KnowmapConfig:
    return KnowmapConfig(
        id=_CONFIG_ID,
        project_id=_PROJECT_ID,
        name="km",
        builder_key_group_id=uuid.uuid4(),
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params={"chunk_size_tokens": 512, "chunk_overlap_tokens": 64},
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        embed_dim=1536,
        last_build_at=_NOW,
        last_build_state=BuildState.QDRANT_COMMITTED,
        last_build_error=None,
        created_at=_NOW,
        deleted_at=None,
    )


def _make_document(
    *, status: DocumentStatus = DocumentStatus.INGESTING, sha: str = "abc123", doc_id: uuid.UUID | None = None
) -> KnowmapDocument:
    return KnowmapDocument(
        id=doc_id or uuid.uuid4(),
        knowmap_config_id=_CONFIG_ID,
        filename="test.txt",
        mime="text/plain",
        size_bytes=100,
        sha256=sha,
        minio_path=f"knowmap-sources/{_PROJECT_ID}/{_CONFIG_ID}/{sha}",
        status=status,
        scan_status=ScanStatus.PENDING,
        scan_at=None,
        uploaded_by=_USER_ID,
        uploaded_at=_NOW,
    )


def _make_service(
    *,
    config_repo: AsyncMock,
    doc_repo: AsyncMock,
    chunk_repo: AsyncMock,
    blob: AsyncMock | None = None,
) -> KnowmapIngestService:
    svc = KnowmapIngestService(
        AsyncMock(),
        blob=blob or AsyncMock(),
        embedder=MagicMock(vector_size=1536),
    )
    svc._configs = config_repo
    svc._docs = doc_repo
    svc._chunks = chunk_repo
    return svc


def _ipt(data: bytes = b"hello world") -> KnowmapIngestInput:
    return KnowmapIngestInput(
        knowmap_config_id=_CONFIG_ID,
        filename="test.txt",
        mime="text/plain",
        data=data,
        uploaded_by=_USER_ID,
    )


class TestIngest:
    async def test_ready_duplicate_returns_early_without_reupload(self) -> None:
        # AC-1: SHA dedup — an already-READY blob is returned without re-storing or
        # re-indexing, and no build is triggered.
        existing = _make_document(status=DocumentStatus.READY)
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        blob = AsyncMock()
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=AsyncMock(), blob=blob)

        with patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()) as build:
            out = await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        assert out is existing
        blob.put.assert_not_called()
        doc_repo.create.assert_not_called()
        build.assert_not_called()

    async def test_missing_config_raises(self) -> None:
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = None
        svc = _make_service(config_repo=cfg_repo, doc_repo=AsyncMock(), chunk_repo=AsyncMock())
        with pytest.raises(KnowmapConfigNotFound):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

    async def test_new_document_chunks_without_qdrant_and_triggers_build(self) -> None:
        # AC-1/AC-6: parse -> chunk -> persist knowmap_chunks (no Qdrant), flip to
        # READY, enqueue scan + build.
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        doc = _make_document(status=DocumentStatus.INGESTING)
        ready = _make_document(status=DocumentStatus.READY, doc_id=doc.id)
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = None
        doc_repo.create.return_value = doc
        doc_repo.get.return_value = ready
        chunk_repo = AsyncMock()
        blob = AsyncMock()
        blob.put.return_value = doc.minio_path
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=chunk_repo, blob=blob)

        with (
            patch.dict(f"{_MOD}.MIME_TO_PARSER", {"text/plain": lambda b: "parsed body"}, clear=False),
            patch(f"{_MOD}.chunk_document", AsyncMock(return_value=["p0", "p1"])),
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()) as scan,
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()) as build,
        ):
            out = await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip="1.2.3.4")

        assert out.status is DocumentStatus.READY
        blob.put.assert_awaited_once()
        # Chunks persisted with contiguous chunk_idx, no Qdrant port on the service.
        chunk_repo.delete_for_document.assert_awaited_once_with(doc.id)
        rows = chunk_repo.insert_many.call_args.args[0]
        assert [r["chunk_idx"] for r in rows] == [0, 1]
        assert not hasattr(svc, "_qdrant")
        doc_repo.set_status.assert_awaited_with(document_id=doc.id, status=DocumentStatus.READY)
        scan.assert_awaited_once()
        # Build enqueued with the config's dedup nonce (state + last_build_at).
        build.assert_awaited_once_with(
            config_id=cfg.id, last_build_state=cfg.last_build_state, last_build_at=cfg.last_build_at
        )

    async def test_index_failure_persists_failed_status_durably(self) -> None:
        # Regression: a sync ingest failure must commit the FAILED status (roll back
        # the partial parse/chunk writes first) — the caller commits only on success,
        # so without an explicit commit here the request session would discard it.
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        doc = _make_document(status=DocumentStatus.INGESTING)
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = None
        doc_repo.create.return_value = doc
        chunk_repo = AsyncMock()
        blob = AsyncMock()
        blob.put.return_value = doc.minio_path
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=chunk_repo, blob=blob)

        with (
            patch.dict(f"{_MOD}.MIME_TO_PARSER", {"text/plain": lambda b: "parsed"}, clear=False),
            patch(f"{_MOD}.chunk_document", AsyncMock(side_effect=RuntimeError("boom"))),
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()),
            pytest.raises(IngestFailed),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        svc._db.rollback.assert_awaited()
        doc_repo.set_status.assert_awaited_with(document_id=doc.id, status=DocumentStatus.FAILED)
        svc._db.commit.assert_awaited()
