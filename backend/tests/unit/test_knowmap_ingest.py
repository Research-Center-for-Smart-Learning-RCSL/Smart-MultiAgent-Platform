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
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, DEFAULT, AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from contexts.knowledge.application.knowmap_ingest_service import (
    KnowmapIngestInput,
    KnowmapIngestService,
)
from contexts.knowledge.domain.errors import (
    DocumentAllowlistConflict,
    DocumentUnprocessable,
    IngestFailed,
    KnowmapConfigNotFound,
    KnowmapDocumentNotFound,
)
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.knowmap import KnowmapConfig, KnowmapDocument
from contexts.knowledge.domain.models import ChunkStrategy, DocumentStatus, IngestClaim, ScanStatus
from contexts.knowledge.domain.reupload import ReuploadAction
from shared_kernel.text_extraction.parsers import ParserError

_MOD = "contexts.knowledge.application.knowmap_ingest_service"
_NOW = datetime(2026, 7, 7, 12, 0, 0)
_PROJECT_ID = uuid.uuid4()
_CONFIG_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _claim(attempt: int = 1) -> IngestClaim:
    return IngestClaim(
        attempt=attempt,
        token=uuid.uuid4(),
        until=datetime.now(UTC) + timedelta(minutes=30),
    )


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
    *,
    status: DocumentStatus = DocumentStatus.INGESTING,
    sha: str = "abc123",
    doc_id: uuid.UUID | None = None,
    scan_status: ScanStatus = ScanStatus.PENDING,
    agent_ids: tuple[uuid.UUID, ...] = (),
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
        scan_status=scan_status,
        scan_at=None,
        uploaded_by=_USER_ID,
        uploaded_at=_NOW,
        agent_ids=agent_ids,
    )


def _make_service(
    *,
    config_repo: AsyncMock,
    doc_repo: AsyncMock,
    chunk_repo: AsyncMock,
    blob: AsyncMock | None = None,
) -> KnowmapIngestService:
    claim = _claim()
    if doc_repo.claim_for_reingest._mock_return_value is DEFAULT:
        doc_repo.claim_for_reingest.return_value = claim
    if doc_repo.claim_initial._mock_return_value is DEFAULT:
        doc_repo.claim_initial.return_value = IngestClaim(
            attempt=0,
            token=uuid.uuid4(),
            until=claim.until,
        )
    doc_repo.owns_claim.return_value = True
    return KnowmapIngestService(
        AsyncMock(),
        blob=blob or AsyncMock(),
        embedder=MagicMock(vector_size=1536),
        configs=config_repo,
        documents=doc_repo,
        chunks=chunk_repo,
        chunker=AsyncMock(return_value=["chunk"]),
        scan_required=False,
    )


def _ipt(
    data: bytes = b"hello world",
    *,
    agent_ids: tuple[uuid.UUID, ...] = (),
) -> KnowmapIngestInput:
    return KnowmapIngestInput(
        knowmap_config_id=_CONFIG_ID,
        filename="test.txt",
        mime="text/plain",
        data=data,
        uploaded_by=_USER_ID,
        agent_ids=agent_ids,
    )


class TestIngest:
    async def test_stale_worker_claim_performs_no_indexing_work(self) -> None:
        doc = _make_document(status=DocumentStatus.INGESTING)
        doc_repo = AsyncMock()
        doc_repo.get.return_value = doc
        doc_repo.owns_claim.return_value = False
        blob = AsyncMock()
        chunk_repo = AsyncMock()
        svc = _make_service(
            config_repo=AsyncMock(),
            doc_repo=doc_repo,
            chunk_repo=chunk_repo,
            blob=blob,
        )
        doc_repo.owns_claim.return_value = False
        stale = _claim(attempt=7)

        returned = await svc.process_document(document_id=doc.id, claim=stale)

        assert returned is doc
        doc_repo.lock_for_ingest.assert_awaited_once_with(doc.id)
        doc_repo.owns_claim.assert_awaited_once_with(doc.id, stale)
        blob.get.assert_not_awaited()
        chunk_repo.delete_for_document.assert_not_awaited()

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

        with (
            patch(f"{_MOD}.emit_knowmap_reupload_audit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()) as build,
        ):
            out = await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        assert out is existing
        blob.put.assert_not_called()
        doc_repo.create.assert_not_called()
        build.assert_not_called()

    async def test_failed_reupload_applies_submitted_allowlist(self) -> None:
        agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        existing = _make_document(
            status=DocumentStatus.FAILED,
            agent_ids=(agent_a,),
        )
        updated = _make_document(
            status=DocumentStatus.READY,
            doc_id=existing.id,
            agent_ids=(agent_a, agent_b),
        )
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        doc_repo.set_agents.return_value = updated
        doc_repo.claim_for_reingest.return_value = _claim()
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=AsyncMock())
        svc._index_document = AsyncMock(return_value=updated)

        with (
            patch(
                f"{_MOD}.emit_knowmap_reupload_audit",
                AsyncMock(),
            ) as reupload_audit,
            patch(
                f"{_MOD}.emit_knowmap_reupload_agents_set_audit",
                AsyncMock(),
            ) as agents_set_audit,
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()),
        ):
            returned = await svc.ingest(
                ipt=_ipt(agent_ids=(agent_a, agent_b)),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        doc_repo.set_agents.assert_awaited_once_with(
            document_id=existing.id,
            agent_ids=(agent_a, agent_b),
        )
        assert reupload_audit.await_args.kwargs["outcome"] is ReuploadAction.REINDEX_WITH_OVERWRITE
        assert reupload_audit.await_args.kwargs["submitted_agent_ids"] == (agent_a, agent_b)
        assert agents_set_audit.await_args.kwargs["doc"] is updated
        assert returned.agent_ids == (agent_a, agent_b)

    async def test_failed_reupload_losing_claim_coalesces_without_indexing(self) -> None:
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        existing = _make_document(status=DocumentStatus.FAILED)
        updated = _make_document(
            status=DocumentStatus.INGESTING,
            doc_id=existing.id,
        )
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        doc_repo.set_agents.return_value = updated
        doc_repo.claim_for_reingest.return_value = None
        doc_repo.get.return_value = updated
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=AsyncMock())
        svc._index_document = AsyncMock()

        with (
            patch(f"{_MOD}.emit_knowmap_reupload_audit", AsyncMock()),
            patch(f"{_MOD}.emit_knowmap_reupload_agents_set_audit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()) as scan,
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()) as build,
        ):
            returned = await svc.ingest(
                ipt=_ipt(),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        assert returned is updated
        doc_repo.claim_for_reingest.assert_awaited_once_with(existing.id)
        doc_repo.get.assert_awaited_once_with(existing.id)
        svc._index_document.assert_not_awaited()
        scan.assert_not_awaited()
        build.assert_not_awaited()

    async def test_reupload_deleted_before_allowlist_update_is_typed_not_found(self) -> None:
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        existing = _make_document(status=DocumentStatus.FAILED)
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        doc_repo.set_agents.return_value = None
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=AsyncMock())

        with (
            patch(f"{_MOD}.emit_knowmap_reupload_audit", AsyncMock()),
            pytest.raises(KnowmapDocumentNotFound, match=str(existing.id)),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

    async def test_live_ingesting_reupload_coalesces_without_indexing(self) -> None:
        agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        existing = _make_document(status=DocumentStatus.INGESTING, agent_ids=(agent_a,))
        updated = _make_document(
            status=DocumentStatus.INGESTING,
            doc_id=existing.id,
            agent_ids=(agent_a, agent_b),
        )
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        doc_repo.set_agents.return_value = updated
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=AsyncMock())
        svc._index_document = AsyncMock()

        with (
            patch(f"{_MOD}.emit_knowmap_reupload_audit", AsyncMock()),
            patch(f"{_MOD}.emit_knowmap_reupload_agents_set_audit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()) as scan,
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()) as build,
        ):
            returned = await svc.ingest(
                ipt=_ipt(agent_ids=(agent_a, agent_b)),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        assert returned is updated
        svc._index_document.assert_not_awaited()
        scan.assert_not_awaited()
        build.assert_not_awaited()

    async def test_create_race_applies_allowlist_without_duplicate_indexing(self) -> None:
        agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        winner = _make_document(
            status=DocumentStatus.INGESTING,
            agent_ids=(agent_a,),
        )
        updated = _make_document(
            status=DocumentStatus.INGESTING,
            doc_id=winner.id,
            agent_ids=(agent_a, agent_b),
        )
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.side_effect = [None, winner]
        doc_repo.create.side_effect = IntegrityError("insert", {}, Exception("duplicate"))
        doc_repo.set_agents.return_value = updated
        blob = AsyncMock()
        blob.put.return_value = winner.minio_path
        svc = _make_service(
            config_repo=cfg_repo,
            doc_repo=doc_repo,
            chunk_repo=AsyncMock(),
            blob=blob,
        )
        svc._index_document = AsyncMock()

        with (
            patch(f"{_MOD}.emit_knowmap_reupload_audit", AsyncMock()),
            patch(f"{_MOD}.emit_knowmap_reupload_agents_set_audit", AsyncMock()),
        ):
            returned = await svc.ingest(
                ipt=_ipt(agent_ids=(agent_a, agent_b)),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        assert returned is updated
        doc_repo.set_agents.assert_awaited_once_with(
            document_id=winner.id,
            agent_ids=(agent_a, agent_b),
        )
        svc._index_document.assert_not_awaited()
        svc._db.rollback.assert_awaited_once()
        svc._db.commit.assert_awaited_once()

    async def test_ready_duplicate_with_different_allowlist_conflicts(self) -> None:
        agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        existing = _make_document(
            status=DocumentStatus.READY,
            agent_ids=(agent_a,),
        )
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=AsyncMock())

        with (
            patch(f"{_MOD}.emit_knowmap_reupload_audit", AsyncMock()) as reupload_audit,
            pytest.raises(DocumentAllowlistConflict, match=r"/api/knowmap-documents/.+/agents"),
        ):
            await svc.ingest(
                ipt=_ipt(agent_ids=(agent_a, agent_b)),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        reupload_audit.assert_awaited_once()
        doc_repo.set_agents.assert_not_awaited()
        svc._db.commit.assert_awaited_once()

    async def test_missing_config_raises(self) -> None:
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = None
        svc = _make_service(config_repo=cfg_repo, doc_repo=AsyncMock(), chunk_repo=AsyncMock())
        with pytest.raises(KnowmapConfigNotFound):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

    async def test_new_document_chunks_and_scans_without_building_while_pending(self) -> None:
        # F-5 (AC-3): parse -> chunk -> persist knowmap_chunks (no Qdrant), flip to
        # READY, enqueue the scan — but NOT the build while the scan verdict is still
        # pending. A never-cleanly-scanned document must never be built; the scan
        # worker's clean-verdict path enqueues the build once the verdict is CLEAN.
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        doc = _make_document(status=DocumentStatus.INGESTING)
        # The refreshed document is READY but still scan_status=PENDING (fresh upload).
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
            patch.object(svc, "_chunker", AsyncMock(return_value=["p0", "p1"])),
            patch(f"{_MOD}.audit.emit", AsyncMock()) as audit_emit,
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
        doc_repo.finish_claim.assert_awaited_with(
            document_id=doc.id,
            claim=ANY,
            status=DocumentStatus.READY,
        )
        # The scan is always enqueued; the build is deferred until the clean verdict.
        scan.assert_not_awaited()
        build.assert_not_called()
        uploaded = next(
            call.args[1]
            for call in audit_emit.await_args_list
            if call.args[1].action == "knowmap.document_uploaded"
        )
        assert uploaded.metadata["agent_ids"] == []

    async def test_reindex_of_clean_document_enqueues_build(self) -> None:
        # F-5: a reindex of an already-clean document (same content/sha) is safe to
        # build immediately — the existing clean verdict still holds — so the
        # indexing-complete site enqueues here rather than waiting for a re-scan.
        cfg = _make_config()
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = cfg
        existing = _make_document(status=DocumentStatus.FAILED)
        clean_ready = _make_document(
            status=DocumentStatus.READY, doc_id=existing.id, scan_status=ScanStatus.CLEAN
        )
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = existing
        doc_repo.set_agents.return_value = existing
        doc_repo.get.return_value = clean_ready
        chunk_repo = AsyncMock()
        blob = AsyncMock()
        svc = _make_service(config_repo=cfg_repo, doc_repo=doc_repo, chunk_repo=chunk_repo, blob=blob)

        with (
            patch.dict(f"{_MOD}.MIME_TO_PARSER", {"text/plain": lambda b: "parsed body"}, clear=False),
            patch.object(svc, "_chunker", AsyncMock(return_value=["p0"])),
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()) as scan,
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()) as build,
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        scan.assert_not_awaited()
        # F-12: the reindex enqueue targets the config's current corpus revision
        # (re-read fresh after the mutation commit) rather than the old (state,
        # epoch) nonce.
        build.assert_awaited_once_with(config_id=cfg.id, target_revision=cfg.corpus_revision)

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
            patch(f"{_MOD}.parse_path", return_value="parsed"),
            patch.object(svc, "_chunker", AsyncMock(side_effect=RuntimeError("boom"))),
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()),
            pytest.raises(IngestFailed),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        svc._db.rollback.assert_awaited()
        doc_repo.finish_claim.assert_awaited_with(
            document_id=doc.id,
            claim=ANY,
            status=DocumentStatus.FAILED,
            failure_code="ingest_failed",
        )
        svc._db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_scan_dispatch_failure_terminally_fails_owned_knowmap_claim(self) -> None:
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        doc = _make_document(status=DocumentStatus.INGESTING)
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = None
        doc_repo.create.return_value = doc
        blob = AsyncMock()
        blob.put.return_value = doc.minio_path
        svc = _make_service(
            config_repo=cfg_repo,
            doc_repo=doc_repo,
            chunk_repo=AsyncMock(),
            blob=blob,
        )
        svc._scan_required = True

        with (
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(
                f"{_MOD}.enqueue_knowmap_scan",
                AsyncMock(side_effect=ConnectionError("redis unavailable")),
            ),
            pytest.raises(IngestFailed, match="scan dispatch"),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        doc_repo.finish_claim.assert_awaited_once_with(
            document_id=doc.id,
            claim=ANY,
            status=DocumentStatus.FAILED,
            failure_code="ingest_failed",
        )

    @pytest.mark.asyncio
    async def test_scan_required_defers_knowmap_parser_and_indexing_until_clean(self) -> None:
        cfg_repo = AsyncMock()
        cfg_repo.get.return_value = _make_config()
        doc = _make_document(status=DocumentStatus.INGESTING)
        doc_repo = AsyncMock()
        doc_repo.find_by_sha.return_value = None
        doc_repo.create.return_value = doc
        chunk_repo = AsyncMock()
        blob = AsyncMock()
        blob.put.return_value = doc.minio_path
        svc = _make_service(
            config_repo=cfg_repo,
            doc_repo=doc_repo,
            chunk_repo=chunk_repo,
            blob=blob,
        )
        svc._scan_required = True
        svc._index_document = AsyncMock()

        with (
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()) as scan,
        ):
            returned = await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        assert returned is doc
        svc._index_document.assert_not_awaited()
        scan.assert_awaited_once_with(
            document_id=doc.id,
            ingest_attempt=0,
            claim_token=ANY,
        )

    async def test_parse_failure_raises_document_unprocessable_not_ingest_failed(self) -> None:
        # A ParserError (unparseable / no text layer / unsupported content) is a
        # client-fixable input problem: it must surface as DocumentUnprocessable (422),
        # not IngestFailed (500), while still persisting the row as FAILED so the file
        # stays visible in the list rather than vanishing.
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

        def _unparseable(*_args) -> str:
            raise ParserError("pdf parse failed: no extractable text layer")

        with (
            patch(f"{_MOD}.parse_path", _unparseable),
            patch(f"{_MOD}.audit.emit", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_scan", AsyncMock()),
            patch(f"{_MOD}.enqueue_knowmap_build", AsyncMock()),
            pytest.raises(DocumentUnprocessable),
        ):
            await svc.ingest(ipt=_ipt(), actor_user_id=_USER_ID, actor_ip=None)

        # The row is still marked FAILED and committed (not rolled back to nothing).
        doc_repo.finish_claim.assert_awaited_with(
            document_id=doc.id,
            claim=ANY,
            status=DocumentStatus.FAILED,
            failure_code="document_unprocessable",
        )
        svc._db.commit.assert_awaited()
