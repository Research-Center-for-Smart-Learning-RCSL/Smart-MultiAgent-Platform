from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.workers.tasks.knowmap as knowmap_tasks
import app.workers.tasks.rag as rag_tasks
from contexts.knowledge.domain.models import DocumentStatus, IngestClaim, ScanStatus


class _Session:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _doc() -> SimpleNamespace:
    return SimpleNamespace(
        ingest_attempt=2,
        ingest_claim_token=uuid.uuid4(),
        ingest_claim_until=datetime.max.replace(tzinfo=UTC),
        status=DocumentStatus.INGESTING,
        scan_status=ScanStatus.PENDING,
    )


class _Context:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Db:
    def begin(self) -> _Context:
        return _Context(self)


class _SessionMaker:
    def __call__(self) -> _Context:
        return _Context(_Db())


@pytest.mark.asyncio
async def test_stale_rag_scan_attempt_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc()

    class _Repo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, document_id: uuid.UUID) -> object:
            return doc

        async def owns_claim(self, document_id: uuid.UUID, claim: object) -> bool:
            return False

    monkeypatch.setattr(rag_tasks, "get_sessionmaker", lambda: _Session)
    monkeypatch.setattr(rag_tasks, "RagDocumentRepository", _Repo)

    result = await rag_tasks.rag_scan_document(
        {},
        document_id=str(uuid.uuid4()),
        ingest_attempt=1,
        claim_token=str(uuid.uuid4()),
    )

    assert result == "stale"


@pytest.mark.asyncio
async def test_stale_knowmap_scan_attempt_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc()

    class _Repo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, document_id: uuid.UUID) -> object:
            return doc

        async def owns_claim(self, document_id: uuid.UUID, claim: object) -> bool:
            return False

    monkeypatch.setattr(knowmap_tasks, "get_sessionmaker", lambda: _Session)
    monkeypatch.setattr(knowmap_tasks, "KnowmapDocumentRepository", _Repo)

    result = await knowmap_tasks.knowmap_scan_document(
        {},
        document_id=str(uuid.uuid4()),
        ingest_attempt=1,
        claim_token=str(uuid.uuid4()),
    )

    assert result == "stale"


@pytest.mark.asyncio
async def test_final_rag_download_failure_terminally_fails_owned_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = _doc()
    doc.size_bytes = 5
    doc.minio_path = "bucket/key"
    doc.rag_config_id = uuid.uuid4()
    marked: list[dict[str, object]] = []

    class _Repo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, document_id: uuid.UUID) -> object:
            return doc

        async def owns_claim(self, document_id: uuid.UUID, claim: object) -> bool:
            return True

        async def mark_scan_owned(self, **kwargs: object) -> bool:
            marked.append(kwargs)
            return True

    scanner = SimpleNamespace(scan_file=AsyncMock())
    failed_emit = AsyncMock()
    settings = SimpleNamespace(security=SimpleNamespace(file_scan_enabled=True, clamav_max_scan_bytes=1000))
    monkeypatch.setattr(rag_tasks, "get_sessionmaker", lambda: _SessionMaker())
    monkeypatch.setattr(rag_tasks, "RagDocumentRepository", _Repo)
    monkeypatch.setattr(
        rag_tasks,
        "Publisher",
        lambda channel: SimpleNamespace(emit=failed_emit),
    )
    monkeypatch.setattr(rag_tasks, "get_settings", lambda: settings)
    monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: scanner)
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=AsyncMock(side_effect=ConnectionError("object store down"))),
    )

    with pytest.raises(ConnectionError, match="object store down"):
        await rag_tasks.rag_scan_document(
            {"job_try": rag_tasks._SCAN_MAX_TRIES},
            document_id=str(uuid.uuid4()),
            ingest_attempt=doc.ingest_attempt,
            claim_token=str(doc.ingest_claim_token),
        )

    assert marked[-1]["scan_status"] is ScanStatus.SKIPPED
    assert marked[-1]["terminal_status"] is DocumentStatus.FAILED
    failed_emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_clean_dispatch_failure_terminally_fails_both_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = IngestClaim(
        attempt=3,
        token=uuid.uuid4(),
        until=datetime.max.replace(tzinfo=UTC),
    )
    rag_finish = AsyncMock(return_value=True)
    knowmap_finish = AsyncMock(return_value=True)
    monkeypatch.setattr(
        rag_tasks,
        "_enqueue_rag_ingest_after_clean",
        AsyncMock(side_effect=ConnectionError("redis down")),
    )
    monkeypatch.setattr(rag_tasks, "_finish_rag_scan_claim", rag_finish)
    monkeypatch.setattr(
        knowmap_tasks,
        "_enqueue_knowmap_ingest_after_clean",
        AsyncMock(side_effect=ConnectionError("redis down")),
    )
    monkeypatch.setattr(knowmap_tasks, "_finish_knowmap_scan_claim", knowmap_finish)

    with pytest.raises(ConnectionError, match="redis down"):
        await rag_tasks._enqueue_rag_ingest_guarded(
            {"job_try": rag_tasks._SCAN_MAX_TRIES},
            uuid.uuid4(),
            claim,
        )
    with pytest.raises(ConnectionError, match="redis down"):
        await knowmap_tasks._enqueue_knowmap_ingest_guarded(
            {"job_try": knowmap_tasks._SCAN_MAX_TRIES},
            uuid.uuid4(),
            uuid.uuid4(),
            claim,
            prior_clean=False,
        )

    assert rag_finish.await_args.kwargs["failure_code"] == "ingest_failed"
    assert knowmap_finish.await_args.kwargs["failure_code"] == "ingest_failed"
