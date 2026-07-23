"""Unit tests for IngestService failure semantics (File RAG).

Mirrors the Knowledge Map ingest tests: a parse failure must surface as
DocumentUnprocessable (422), any other ingest failure as IngestFailed (500),
and in both cases the row is persisted FAILED (committed before indexing) so a
failed upload stays visible in the list instead of vanishing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.knowledge.application.ingest_service import IngestInput, IngestService
from contexts.knowledge.domain.errors import DocumentUnprocessable, IngestFailed
from contexts.knowledge.domain.models import (
    ChunkStrategy,
    DocumentStatus,
    RagConfig,
    RagDocument,
    ScanStatus,
)

_MOD = "contexts.knowledge.application.ingest_service"
_NOW = datetime(2026, 7, 23, 12, 0, 0)
_PROJECT_ID = uuid.uuid4()
_CONFIG_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _make_config() -> RagConfig:
    return RagConfig(
        id=_CONFIG_ID,
        project_id=_PROJECT_ID,
        name="rag",
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params={"chunk_size_tokens": 512, "chunk_overlap_tokens": 64},
        embed_key_id=uuid.uuid4(),
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=False,
        rerank_key_id=None,
        rerank_provider=None,
        rerank_model=None,
        top_k=8,
        created_at=_NOW,
        deleted_at=None,
    )


def _make_document(*, status: DocumentStatus = DocumentStatus.INGESTING, sha: str = "abc123") -> RagDocument:
    return RagDocument(
        id=uuid.uuid4(),
        rag_config_id=_CONFIG_ID,
        filename="test.txt",
        mime="text/plain",
        size_bytes=100,
        sha256=sha,
        minio_path=f"rag-sources/{_PROJECT_ID}/{_CONFIG_ID}/{sha}",
        status=status,
        scan_status=ScanStatus.PENDING,
        scan_at=None,
        uploaded_by=_USER_ID,
        uploaded_at=_NOW,
    )


def _make_service(
    *, config_repo: AsyncMock, doc_repo: AsyncMock, chunk_repo: AsyncMock, blob: AsyncMock | None = None
) -> IngestService:
    svc = IngestService(
        AsyncMock(),
        blob=blob or AsyncMock(),
        embedder=MagicMock(vector_size=1536),
        qdrant=AsyncMock(),
    )
    svc._configs = config_repo
    svc._docs = doc_repo
    svc._chunks = chunk_repo
    return svc


def _ipt(data: bytes = b"hello world") -> IngestInput:
    return IngestInput(
        rag_config_id=_CONFIG_ID,
        filename="test.txt",
        mime="text/plain",
        data=data,
        uploaded_by=_USER_ID,
    )


def _fake_publisher() -> MagicMock:
    pub = MagicMock()
    pub.emit = AsyncMock()
    return MagicMock(return_value=pub)


class TestIngestFailureSemantics:
    async def test_parse_failure_raises_document_unprocessable(self) -> None:
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        doc = _make_document()
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = None
        doc_repo.create.return_value = doc
        chunk_repo = AsyncMock()
        blob = AsyncMock()
        blob.put.return_value = doc.minio_path
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=chunk_repo, blob=blob)

        def _unparseable(_: bytes) -> str:
            from shared_kernel.text_extraction.parsers import ParserError

            raise ParserError("pdf parse failed: no extractable text layer")

        with (
            patch.dict(f"{_MOD}.MIME_TO_PARSER", {"text/plain": _unparseable}, clear=False),
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.Publisher", _fake_publisher()),
            patch(f"{_MOD}.enqueue_rag_scan", AsyncMock()),
            pytest.raises(DocumentUnprocessable),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        # The row is persisted FAILED, not rolled back to nothing.
        doc_repo.set_status.assert_awaited_with(document_id=doc.id, status=DocumentStatus.FAILED)
        svc._db.commit.assert_awaited()

    async def test_reindex_retry_failure_still_records_reupload_audit(self) -> None:
        # The re-upload audit must be committed before indexing, so a failed retry of
        # a previously-failed document is not erased by the FAILED-persist rollback
        # (which would otherwise leave the retry invisible in the audit trail).
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        existing = _make_document(status=DocumentStatus.FAILED)
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        chunk_repo = AsyncMock()
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=chunk_repo)

        with (
            patch.dict(f"{_MOD}.MIME_TO_PARSER", {"text/plain": lambda b: "parsed body"}, clear=False),
            patch(f"{_MOD}.chunk_document", AsyncMock(side_effect=RuntimeError("boom"))),
            patch(f"{_MOD}.emit_reupload_audit", AsyncMock()) as reupload_audit,
            patch(f"{_MOD}.Publisher", _fake_publisher()),
            patch(f"{_MOD}.enqueue_rag_scan", AsyncMock()),
            pytest.raises(IngestFailed),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        reupload_audit.assert_awaited_once()
        # Two commits: the pre-index re-upload-audit commit and the FAILED-persist
        # commit. Before the fix the audit was uncommitted and rolled back on failure.
        assert svc._db.commit.await_count >= 2

    async def test_non_parse_failure_raises_ingest_failed(self) -> None:
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        doc = _make_document()
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = None
        doc_repo.create.return_value = doc
        chunk_repo = AsyncMock()
        blob = AsyncMock()
        blob.put.return_value = doc.minio_path
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=chunk_repo, blob=blob)

        with (
            patch.dict(f"{_MOD}.MIME_TO_PARSER", {"text/plain": lambda b: "parsed body"}, clear=False),
            patch(f"{_MOD}.chunk_document", AsyncMock(side_effect=RuntimeError("boom"))),
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.Publisher", _fake_publisher()),
            patch(f"{_MOD}.enqueue_rag_scan", AsyncMock()),
            pytest.raises(IngestFailed),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        doc_repo.set_status.assert_awaited_with(document_id=doc.id, status=DocumentStatus.FAILED)
        svc._db.commit.assert_awaited()
