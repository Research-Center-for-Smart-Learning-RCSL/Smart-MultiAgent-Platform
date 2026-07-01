"""Unit tests for the RAG ingest + retrieve services.

All infrastructure (Qdrant, MinIO/BlobStore, embedder, reranker, repos)
is mocked via the Protocol-based ports — no real I/O.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.knowledge.application.ingest_service import (
    MAX_MULTIPART_BYTES,
    IngestInput,
    IngestService,
    _normalise_mime,
)
from contexts.knowledge.application.retrieve import RetrievedChunk, RetrieveService
from contexts.knowledge.domain.errors import (
    DocumentTooLarge,
    IngestFailed,
    RagConfigNotFound,
    UnsupportedMime,
)
from contexts.knowledge.domain.models import (
    ChunkStrategy,
    DocumentStatus,
    RagConfig,
    RagDocument,
    ScanStatus,
)

_NOW = datetime(2026, 6, 22, 12, 0, 0)
_PROJECT_ID = uuid.uuid4()
_CONFIG_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _make_config(
    *,
    config_id: uuid.UUID | None = None,
    top_k: int = 5,
    rerank_enabled: bool = False,
) -> RagConfig:
    return RagConfig(
        id=config_id or _CONFIG_ID,
        project_id=_PROJECT_ID,
        name="test-config",
        chunk_strategy=ChunkStrategy.FIXED,
        chunk_params={"chunk_size_tokens": 512, "chunk_overlap_tokens": 64},
        embed_key_id=None,
        embed_provider="openai",
        embed_model="text-embedding-3-small",
        rerank_enabled=rerank_enabled,
        rerank_key_id=None,
        rerank_provider=None,
        rerank_model=None,
        top_k=top_k,
        created_at=_NOW,
        deleted_at=None,
    )


def _make_document(
    *,
    status: DocumentStatus = DocumentStatus.INGESTING,
    sha: str = "abc123",
    doc_id: uuid.UUID | None = None,
) -> RagDocument:
    return RagDocument(
        id=doc_id or uuid.uuid4(),
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


def _make_ingest_service(
    *,
    config_repo: AsyncMock | None = None,
    doc_repo: AsyncMock | None = None,
    chunk_repo: AsyncMock | None = None,
    blob: AsyncMock | None = None,
    embedder: MagicMock | None = None,
    qdrant: AsyncMock | None = None,
) -> IngestService:
    db = AsyncMock()
    svc = IngestService(
        db,
        blob=blob or AsyncMock(),
        embedder=embedder or MagicMock(vector_size=1536),
        qdrant=qdrant or AsyncMock(),
    )
    if config_repo is not None:
        svc._configs = config_repo
    if doc_repo is not None:
        svc._docs = doc_repo
    if chunk_repo is not None:
        svc._chunks = chunk_repo
    return svc


# ---------------------------------------------------------------------------
# _normalise_mime
# ---------------------------------------------------------------------------


class TestNormaliseMime:
    def test_strips_parameters(self) -> None:
        assert _normalise_mime("text/plain; charset=utf-8", "f.txt") == "text/plain"

    def test_falls_back_to_filename(self) -> None:
        assert _normalise_mime("application/octet-stream", "doc.pdf") == "application/pdf"

    def test_preserves_valid_mime(self) -> None:
        assert _normalise_mime("text/markdown", "f.md") == "text/markdown"

    def test_empty_falls_back(self) -> None:
        assert _normalise_mime("", "f.txt") == "text/plain"


# ---------------------------------------------------------------------------
# IngestService.ingest — validation
# ---------------------------------------------------------------------------


class TestIngestValidation:
    async def test_too_large_raises(self) -> None:
        svc = _make_ingest_service()
        with pytest.raises(DocumentTooLarge):
            await svc.ingest(
                ipt=IngestInput(
                    rag_config_id=_CONFIG_ID,
                    filename="big.txt",
                    mime="text/plain",
                    data=b"x" * (MAX_MULTIPART_BYTES + 1),
                    uploaded_by=_USER_ID,
                ),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    async def test_unsupported_mime_raises(self) -> None:
        configs = AsyncMock()
        svc = _make_ingest_service(config_repo=configs)
        with pytest.raises(UnsupportedMime):
            await svc.ingest(
                ipt=IngestInput(
                    rag_config_id=_CONFIG_ID,
                    filename="file.exe",
                    mime="application/x-executable",
                    data=b"MZ",
                    uploaded_by=_USER_ID,
                ),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    async def test_config_not_found_raises(self) -> None:
        configs = AsyncMock()
        configs.get.return_value = None
        svc = _make_ingest_service(config_repo=configs)
        with pytest.raises(RagConfigNotFound):
            await svc.ingest(
                ipt=IngestInput(
                    rag_config_id=_CONFIG_ID,
                    filename="f.txt",
                    mime="text/plain",
                    data=b"hello",
                    uploaded_by=_USER_ID,
                ),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )


# ---------------------------------------------------------------------------
# IngestService.ingest — dedup
# ---------------------------------------------------------------------------


class TestIngestDedup:
    @patch("contexts.knowledge.application.ingest_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.knowledge.application.ingest_service.Publisher")
    async def test_existing_ready_doc_returns_early(self, mock_pub, _audit) -> None:
        mock_pub.return_value = AsyncMock()
        existing = _make_document(status=DocumentStatus.READY)
        configs = AsyncMock()
        configs.get.return_value = _make_config()
        docs = AsyncMock()
        docs.find_by_sha.return_value = existing
        svc = _make_ingest_service(config_repo=configs, doc_repo=docs)

        result = await svc.ingest(
            ipt=IngestInput(
                rag_config_id=_CONFIG_ID,
                filename="f.txt",
                mime="text/plain",
                data=b"hello",
                uploaded_by=_USER_ID,
            ),
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert result.id == existing.id
        docs.create.assert_not_awaited()


# ---------------------------------------------------------------------------
# IngestService.ingest — happy path
# ---------------------------------------------------------------------------


class TestIngestHappyPath:
    @patch("contexts.knowledge.application.ingest_service.enqueue_rag_scan", new_callable=AsyncMock)
    @patch("contexts.knowledge.application.ingest_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.knowledge.application.ingest_service.Publisher")
    @patch(
        "contexts.knowledge.application.ingest_service.chunk_document",
        new_callable=AsyncMock,
        return_value=["chunk1", "chunk2"],
    )
    @patch(
        "contexts.knowledge.application.ingest_service.MIME_TO_PARSER", {"text/plain": lambda d: d.decode()}
    )
    async def test_new_doc_ingested(self, _chunk, mock_pub, _audit, _scan) -> None:
        mock_pub.return_value = AsyncMock()
        cfg = _make_config()
        new_doc = _make_document()
        ready_doc = _make_document(status=DocumentStatus.READY)
        configs = AsyncMock()
        configs.get.return_value = cfg
        docs = AsyncMock()
        docs.find_by_sha.return_value = None
        docs.create.return_value = new_doc
        docs.get.return_value = ready_doc
        chunks = AsyncMock()
        blob = AsyncMock()
        blob.put.return_value = f"rag-sources/{_PROJECT_ID}/{_CONFIG_ID}/sha"
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
        qdrant = AsyncMock()

        svc = _make_ingest_service(
            config_repo=configs,
            doc_repo=docs,
            chunk_repo=chunks,
            blob=blob,
            embedder=embedder,
            qdrant=qdrant,
        )

        result = await svc.ingest(
            ipt=IngestInput(
                rag_config_id=_CONFIG_ID,
                filename="f.txt",
                mime="text/plain",
                data=b"hello world",
                uploaded_by=_USER_ID,
            ),
            actor_user_id=_USER_ID,
            actor_ip="1.2.3.4",
        )

        assert result.status is DocumentStatus.READY
        blob.put.assert_awaited_once()
        docs.create.assert_awaited_once()
        embedder.embed_batch.assert_awaited_once()
        qdrant.upsert_chunks.assert_awaited_once()
        docs.set_status.assert_awaited_once()


# ---------------------------------------------------------------------------
# IngestService.ingest — failure handling
# ---------------------------------------------------------------------------


class TestIngestFailure:
    @patch("contexts.knowledge.application.ingest_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.knowledge.application.ingest_service.Publisher")
    @patch(
        "contexts.knowledge.application.ingest_service.chunk_document",
        new_callable=AsyncMock,
        return_value=["c1"],
    )
    @patch(
        "contexts.knowledge.application.ingest_service.MIME_TO_PARSER", {"text/plain": lambda d: d.decode()}
    )
    async def test_embed_failure_raises_ingest_failed(self, _chunk, mock_pub, _audit) -> None:
        mock_pub.return_value = AsyncMock()
        cfg = _make_config()
        new_doc = _make_document()
        configs = AsyncMock()
        configs.get.return_value = cfg
        docs = AsyncMock()
        docs.find_by_sha.return_value = None
        docs.create.return_value = new_doc
        blob = AsyncMock()
        blob.put.return_value = "path"
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(side_effect=RuntimeError("provider down"))
        qdrant = AsyncMock()

        svc = _make_ingest_service(
            config_repo=configs,
            doc_repo=docs,
            blob=blob,
            embedder=embedder,
            qdrant=qdrant,
        )

        with pytest.raises(IngestFailed, match="provider down"):
            await svc.ingest(
                ipt=IngestInput(
                    rag_config_id=_CONFIG_ID,
                    filename="f.txt",
                    mime="text/plain",
                    data=b"hello",
                    uploaded_by=_USER_ID,
                ),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )


# ---------------------------------------------------------------------------
# IngestService — vector count mismatch
# ---------------------------------------------------------------------------


class TestIngestVectorMismatch:
    @patch("contexts.knowledge.application.ingest_service.enqueue_rag_scan", new_callable=AsyncMock)
    @patch("contexts.knowledge.application.ingest_service.audit.emit", new_callable=AsyncMock)
    @patch("contexts.knowledge.application.ingest_service.Publisher")
    @patch(
        "contexts.knowledge.application.ingest_service.chunk_document",
        new_callable=AsyncMock,
        return_value=["c1", "c2"],
    )
    @patch(
        "contexts.knowledge.application.ingest_service.MIME_TO_PARSER", {"text/plain": lambda d: d.decode()}
    )
    async def test_short_vector_list_raises(self, _chunk, mock_pub, _audit, _scan) -> None:
        mock_pub.return_value = AsyncMock()
        cfg = _make_config()
        new_doc = _make_document()
        configs = AsyncMock()
        configs.get.return_value = cfg
        docs = AsyncMock()
        docs.find_by_sha.return_value = None
        docs.create.return_value = new_doc
        blob = AsyncMock()
        blob.put.return_value = "path"
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])  # 1 vector for 2 chunks
        qdrant = AsyncMock()

        svc = _make_ingest_service(
            config_repo=configs,
            doc_repo=docs,
            blob=blob,
            embedder=embedder,
            qdrant=qdrant,
        )

        with pytest.raises(IngestFailed, match="1 vectors for 2 chunks"):
            await svc.ingest(
                ipt=IngestInput(
                    rag_config_id=_CONFIG_ID,
                    filename="f.txt",
                    mime="text/plain",
                    data=b"hello",
                    uploaded_by=_USER_ID,
                ),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )


# ---------------------------------------------------------------------------
# RetrieveService
# ---------------------------------------------------------------------------


def _make_retrieve_service(
    *,
    config_repo: AsyncMock | None = None,
    chunk_repo: AsyncMock | None = None,
    docs_repo: AsyncMock | None = None,
    embedder: MagicMock | None = None,
    qdrant: AsyncMock | None = None,
    reranker: AsyncMock | None = None,
) -> RetrieveService:
    db = AsyncMock()
    svc = RetrieveService(
        db,
        embedder=embedder or MagicMock(vector_size=3),
        qdrant=qdrant or AsyncMock(),
        reranker=reranker,
    )
    if config_repo is not None:
        svc._configs = config_repo
    if chunk_repo is not None:
        svc._chunks = chunk_repo
    if docs_repo is None:
        # Default: a non-empty doc set so retrieval proceeds past the config/
        # agent scoping gate. Tests that exercise scoping inject their own.
        docs_repo = AsyncMock()
        docs_repo.retrievable_document_ids.return_value = [uuid.uuid4()]
        docs_repo.allowed_document_ids.return_value = [uuid.uuid4()]
    svc._docs = docs_repo
    return svc


class TestRetrieveQuery:
    async def test_config_not_found_raises(self) -> None:
        configs = AsyncMock()
        configs.get.return_value = None
        svc = _make_retrieve_service(config_repo=configs)

        with pytest.raises(RagConfigNotFound):
            await svc.query(config_id=_CONFIG_ID, text="test", allow_unrestricted=True)

    async def test_empty_results(self) -> None:
        configs = AsyncMock()
        configs.get.return_value = _make_config()
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        qdrant = AsyncMock()
        qdrant.search.return_value = []
        svc = _make_retrieve_service(config_repo=configs, embedder=embedder, qdrant=qdrant)

        result = await svc.query(config_id=_CONFIG_ID, text="test", allow_unrestricted=True)

        assert result == []

    async def test_returns_hydrated_chunks(self) -> None:
        cfg = _make_config(top_k=2)
        configs = AsyncMock()
        configs.get.return_value = cfg
        point1 = uuid.uuid4()
        point2 = uuid.uuid4()
        doc_id = uuid.uuid4()
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        qdrant = AsyncMock()
        hit1 = MagicMock(point_id=point1, score=0.9)
        hit2 = MagicMock(point_id=point2, score=0.7)
        qdrant.search.return_value = [hit1, hit2]
        chunks = AsyncMock()
        chunk_row1 = MagicMock(qdrant_point_id=point1, chunk_idx=0, document_id=doc_id, text="first")
        chunk_row2 = MagicMock(qdrant_point_id=point2, chunk_idx=1, document_id=doc_id, text="second")
        chunks.lookup_points.return_value = [chunk_row1, chunk_row2]

        svc = _make_retrieve_service(config_repo=configs, embedder=embedder, qdrant=qdrant, chunk_repo=chunks)

        result = await svc.query(config_id=_CONFIG_ID, text="question", allow_unrestricted=True)

        assert len(result) == 2
        assert result[0].text == "first"
        assert result[0].score == 0.9
        assert result[1].text == "second"

    async def test_with_reranker(self) -> None:
        from contexts.knowledge.application.ports import RerankResult

        cfg = _make_config(top_k=1, rerank_enabled=True)
        configs = AsyncMock()
        configs.get.return_value = cfg
        point1 = uuid.uuid4()
        point2 = uuid.uuid4()
        doc_id = uuid.uuid4()
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        qdrant = AsyncMock()
        qdrant.search.return_value = [
            MagicMock(point_id=point1, score=0.9),
            MagicMock(point_id=point2, score=0.7),
        ]
        chunks = AsyncMock()
        chunks.lookup_points.return_value = [
            MagicMock(qdrant_point_id=point1, chunk_idx=0, document_id=doc_id, text="first"),
            MagicMock(qdrant_point_id=point2, chunk_idx=1, document_id=doc_id, text="second"),
        ]
        reranker = AsyncMock()
        reranker.rerank.return_value = [RerankResult(index=1, score=0.95)]

        svc = _make_retrieve_service(
            config_repo=configs,
            embedder=embedder,
            qdrant=qdrant,
            chunk_repo=chunks,
            reranker=reranker,
        )

        result = await svc.query(config_id=_CONFIG_ID, text="question", allow_unrestricted=True)

        assert len(result) == 1
        assert result[0].text == "second"
        assert result[0].score == 0.95
        reranker.rerank.assert_awaited_once()

    async def test_top_k_from_config(self) -> None:
        cfg = _make_config(top_k=1)
        configs = AsyncMock()
        configs.get.return_value = cfg
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        point1 = uuid.uuid4()
        point2 = uuid.uuid4()
        doc_id = uuid.uuid4()
        qdrant = AsyncMock()
        qdrant.search.return_value = [
            MagicMock(point_id=point1, score=0.9),
            MagicMock(point_id=point2, score=0.7),
        ]
        chunks = AsyncMock()
        chunks.lookup_points.return_value = [
            MagicMock(qdrant_point_id=point1, chunk_idx=0, document_id=doc_id, text="first"),
            MagicMock(qdrant_point_id=point2, chunk_idx=1, document_id=doc_id, text="second"),
        ]
        svc = _make_retrieve_service(config_repo=configs, embedder=embedder, qdrant=qdrant, chunk_repo=chunks)

        result = await svc.query(config_id=_CONFIG_ID, text="q", allow_unrestricted=True)

        assert len(result) == 1

    async def test_agent_scope_constrains_qdrant_to_allowed_docs(self) -> None:
        # With an agent_id, retrieval resolves the agent's visible documents up
        # front and constrains the Qdrant search to them (doc_ids), so the vector
        # top_k is computed over allowed docs — correct recall, no over-fetch.
        cfg = _make_config(top_k=3)
        configs = AsyncMock()
        configs.get.return_value = cfg
        allowed = [uuid.uuid4(), uuid.uuid4()]
        docs = AsyncMock()
        docs.allowed_document_ids.return_value = allowed
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        qdrant = AsyncMock()
        qdrant.search.return_value = [MagicMock(point_id=uuid.uuid4(), score=0.9)]
        chunks = AsyncMock()
        chunks.lookup_points.return_value = []
        agent_id = uuid.uuid4()

        svc = _make_retrieve_service(
            config_repo=configs, docs_repo=docs, embedder=embedder, qdrant=qdrant, chunk_repo=chunks
        )
        await svc.query(config_id=_CONFIG_ID, text="q", agent_id=agent_id)

        # Allowed-doc set was resolved for this agent and passed to Qdrant.
        assert docs.allowed_document_ids.await_args.kwargs["agent_id"] == agent_id
        assert qdrant.search.await_args.kwargs["doc_ids"] == allowed
        # No over-fetch without reranking — Qdrant already scopes to allowed docs.
        assert qdrant.search.await_args.kwargs["top_k"] == 3
        # lookup_points no longer takes an agent_id (scoping moved up front).
        assert "agent_id" not in chunks.lookup_points.await_args.kwargs

    async def test_agent_with_no_allowed_docs_returns_empty_without_search(self) -> None:
        cfg = _make_config(top_k=3)
        configs = AsyncMock()
        configs.get.return_value = cfg
        docs = AsyncMock()
        docs.allowed_document_ids.return_value = []  # agent on no allowlist
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        qdrant = AsyncMock()

        svc = _make_retrieve_service(
            config_repo=configs, docs_repo=docs, embedder=embedder, qdrant=qdrant
        )
        result = await svc.query(config_id=_CONFIG_ID, text="q", agent_id=uuid.uuid4())

        assert result == []
        qdrant.search.assert_not_awaited()  # short-circuits before embedding/search

    async def test_query_without_agent_id_requires_opt_in(self) -> None:
        # Omitting agent_id silently bypasses the per-document allowlist, so it
        # must be an explicit error unless allow_unrestricted is set.
        svc = _make_retrieve_service(config_repo=AsyncMock())
        with pytest.raises(ValueError, match="agent_id"):
            await svc.query(config_id=_CONFIG_ID, text="q")

    async def test_reranked_results_sorted_by_score(self) -> None:
        from contexts.knowledge.application.ports import RerankResult

        cfg = _make_config(top_k=3, rerank_enabled=True)
        configs = AsyncMock()
        configs.get.return_value = cfg
        pts = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        doc_id = uuid.uuid4()
        embedder = MagicMock(vector_size=3)
        embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        qdrant = AsyncMock()
        qdrant.search.return_value = [MagicMock(point_id=p, score=0.5) for p in pts]
        chunks = AsyncMock()
        chunks.lookup_points.return_value = [
            MagicMock(qdrant_point_id=pts[i], chunk_idx=i, document_id=doc_id, text=f"c{i}")
            for i in range(3)
        ]
        reranker = AsyncMock()
        # Returned deliberately out of score order.
        reranker.rerank.return_value = [
            RerankResult(index=0, score=0.10),
            RerankResult(index=1, score=0.90),
            RerankResult(index=2, score=0.50),
        ]
        svc = _make_retrieve_service(
            config_repo=configs, embedder=embedder, qdrant=qdrant, chunk_repo=chunks, reranker=reranker
        )
        result = await svc.query(config_id=_CONFIG_ID, text="q", allow_unrestricted=True)
        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)
        assert scores[0] == 0.90


class TestFormatRagMessage:
    def test_format_single_chunk(self) -> None:
        svc = _make_retrieve_service()
        doc_id = uuid.uuid4()
        chunks = [RetrievedChunk(document_id=doc_id, chunk_idx=0, text="hello", score=0.9)]
        msg = svc.format_as_rag_message(chunks)

        assert msg["role"] == "system"
        assert "hello" in msg["content"]
        assert msg["metadata"]["type"] == "rag"
        assert len(msg["metadata"]["chunk_refs"]) == 1


class TestRagContextProviderSources:
    def test_format_rag_block_includes_source_filename(self) -> None:
        from contexts.knowledge.application.rag_context_provider import _format_rag_block

        doc_id = uuid.uuid4()
        chunks = [
            RetrievedChunk(document_id=doc_id, chunk_idx=2, text="alpha policy", score=0.8765)
        ]
        sources = [
            {
                "document_id": str(doc_id),
                "filename": "guide.pdf",
                "chunk_idx": 2,
                "score": 0.8765,
            }
        ]

        block = _format_rag_block(chunks, sources)

        assert "source=guide.pdf" in block
        assert f"doc={doc_id}" in block
        assert "alpha policy" in block

    async def test_build_sources_resolves_filenames(self, monkeypatch) -> None:
        # The provider shapes retrieved chunks into citable sources (one per
        # chunk, ranked order, filename resolved) that the turn engine persists
        # on the reply metadata for the chat UI.
        from contexts.knowledge.application.rag_context_provider import RagContextProvider

        doc_id = uuid.uuid4()
        repo = AsyncMock()
        repo.get_many.return_value = [MagicMock(id=doc_id, filename="guide.pdf")]
        monkeypatch.setattr(
            "contexts.knowledge.infrastructure.repositories.RagDocumentRepository",
            lambda _db: repo,
        )

        provider = RagContextProvider(AsyncMock(), router=MagicMock())
        chunks = [
            RetrievedChunk(document_id=doc_id, chunk_idx=2, text="a", score=0.8765),
            RetrievedChunk(document_id=doc_id, chunk_idx=5, text="b", score=0.5),
        ]
        sources = await provider._build_sources(chunks)

        assert sources == [
            {"document_id": str(doc_id), "filename": "guide.pdf", "chunk_idx": 2, "score": 0.8765},
            {"document_id": str(doc_id), "filename": "guide.pdf", "chunk_idx": 5, "score": 0.5},
        ]

    async def test_build_sources_tolerates_deleted_document(self, monkeypatch) -> None:
        # A since-deleted document must not block the citation — filename is None.
        from contexts.knowledge.application.rag_context_provider import RagContextProvider

        repo = AsyncMock()
        repo.get_many.return_value = []  # document deleted after retrieval
        monkeypatch.setattr(
            "contexts.knowledge.infrastructure.repositories.RagDocumentRepository",
            lambda _db: repo,
        )
        provider = RagContextProvider(AsyncMock(), router=MagicMock())
        doc_id = uuid.uuid4()
        sources = await provider._build_sources(
            [RetrievedChunk(document_id=doc_id, chunk_idx=0, text="x", score=0.1)]
        )
        assert sources == [
            {"document_id": str(doc_id), "filename": None, "chunk_idx": 0, "score": 0.1}
        ]
