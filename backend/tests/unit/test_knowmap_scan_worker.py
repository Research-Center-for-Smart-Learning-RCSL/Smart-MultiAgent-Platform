"""F-27 — knowmap_scan_document enqueues a rebuild on a SKIPPED verdict.

The scan worker enqueued a rebuild only on QUARANTINED, never on SKIPPED (over-size
or a ClamAV error), even though both verdicts are equally excluded from the build
and retrieval selectors. So a document built during the F-5 pending race and later
marked SKIPPED had its triples left in Neo4j with no rebuild. These tests drive the
worker with all infra mocked and assert the SKIPPED paths now enqueue exactly like
QUARANTINED — the over-size path immediately (terminal) and the ClamAV-error path
only on the retry-exhausted attempt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.workers.tasks.knowmap as km
from contexts.knowledge.domain.models import DocumentStatus, ScanStatus

_CFG = SimpleNamespace(id=uuid.uuid4(), corpus_revision=3)
# F-12 (W1): a scan-verdict rebuild advances the corpus revision before enqueuing.
_BUMPED_REV = _CFG.corpus_revision + 1


class _Begin:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Db:
    def begin(self) -> _Begin:
        return _Begin()


class _Session:
    async def __aenter__(self) -> _Db:
        return _Db()

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _install(monkeypatch, *, doc: object, max_scan_bytes: int, scanner: object) -> dict[str, list]:
    captured: dict[str, list] = {"enqueued": [], "scans": []}

    class _DocRepo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, doc_id: uuid.UUID) -> object:
            return doc

        async def owns_claim(self, document_id, claim) -> bool:
            return True

        async def mark_scan_owned(
            self,
            *,
            document_id,
            claim,
            scan_status,
            scan_at,
            terminal_status=None,
            failure_code=None,
        ) -> bool:
            captured["scans"].append(scan_status)
            # Persist the verdict onto the row like the DB does, so a subsequent
            # arq retry that re-reads the document observes the earlier write. This
            # is what makes the multi-attempt sequence faithful (F-27 durability).
            doc.scan_status = scan_status
            if terminal_status is not None:
                doc.status = terminal_status
            return True

    class _CfgRepo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, cfg_id: uuid.UUID) -> object:
            return _CFG

        async def bump_corpus_revision(self, cfg_id: uuid.UUID) -> int:
            return _BUMPED_REV

    async def _enqueue(**kwargs: Any) -> None:
        captured["enqueued"].append(kwargs)

    settings = SimpleNamespace(
        security=SimpleNamespace(file_scan_enabled=True, clamav_max_scan_bytes=max_scan_bytes)
    )
    monkeypatch.setattr(km, "get_sessionmaker", lambda: _Session)
    monkeypatch.setattr(km, "KnowmapDocumentRepository", _DocRepo)
    monkeypatch.setattr(km, "KnowmapConfigRepository", _CfgRepo)
    monkeypatch.setattr(km, "enqueue_knowmap_build", _enqueue)
    monkeypatch.setattr(km, "enqueue", _afn(None))
    monkeypatch.setattr("app.config.settings.get_settings", lambda: settings)
    monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: scanner)
    return captured


def _doc(*, size: int = 100, scan_status: ScanStatus = ScanStatus.PENDING) -> SimpleNamespace:
    return SimpleNamespace(
        knowmap_config_id=_CFG.id,
        size_bytes=size,
        minio_path="bucket/key",
        status=DocumentStatus.INGESTING,
        scan_status=scan_status,
        ingest_attempt=0,
        ingest_claim_token=uuid.uuid4(),
        ingest_claim_until=datetime.max.replace(tzinfo=UTC),
    )


def _clamav_error_scanner() -> object:
    from shared_kernel.scanning import ScanError

    class _Scanner:
        async def scan_file(self, path: object) -> object:
            raise ScanError("clamav down")

    return _Scanner()


@pytest.mark.asyncio
async def test_final_download_failure_terminally_fails_owned_claim(monkeypatch) -> None:
    doc = _doc(size=5)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=SimpleNamespace())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=AsyncMock(side_effect=ConnectionError("object store down"))),
    )

    with pytest.raises(ConnectionError, match="object store down"):
        await km.knowmap_scan_document(
            {"job_try": km._SCAN_MAX_TRIES},
            document_id=str(uuid.uuid4()),
        )

    assert cap["scans"] == [ScanStatus.SKIPPED]


@pytest.mark.asyncio
async def test_oversize_skipped_rebuilds_when_previously_clean(monkeypatch) -> None:
    # A previously-CLEAN (hence possibly built) document that is now over-size is
    # marked SKIPPED and enqueues one rebuild targeting a freshly bumped corpus
    # revision (F-12 W1) to evict its triples — identical args to the QUARANTINED path.
    doc = _doc(size=100, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=10, scanner=SimpleNamespace())

    result = await km.knowmap_scan_document({}, document_id=str(uuid.uuid4()))

    assert result == "skipped:too_large"
    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == [{"config_id": _CFG.id, "target_revision": _BUMPED_REV}]


@pytest.mark.asyncio
async def test_oversize_skipped_no_rebuild_when_never_clean(monkeypatch) -> None:
    # F-12 (W6): a fresh (never-CLEAN) over-size document was never in the buildable
    # set, so there are no triples to evict — no rebuild is enqueued.
    doc = _doc(size=100, scan_status=ScanStatus.PENDING)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=10, scanner=SimpleNamespace())

    result = await km.knowmap_scan_document({}, document_id=str(uuid.uuid4()))

    assert result == "skipped:too_large"
    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == []


@pytest.mark.asyncio
async def test_clamav_error_evicts_once_on_transition_and_marks_skipped_every_attempt(
    monkeypatch,
) -> None:
    # F-27, driven over the FULL arq retry sequence (the mock persists the verdict
    # across invocations). A previously-CLEAN (hence possibly built) document under
    # a persistent ClamAV error is marked SKIPPED on EVERY attempt — so it leaves
    # the buildable/retrieval set immediately and is never stranded non-terminal by
    # an interrupted retry — and enqueues exactly ONE eviction rebuild, on the first
    # attempt (the CLEAN->SKIPPED transition). Later attempts re-read SKIPPED, so
    # prior_clean is False and no duplicate rebuild is enqueued.
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_clamav_error_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=_afn(None)),
    )

    # Attempt 1: the CLEAN->SKIPPED transition marks SKIPPED and evicts once.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))
    assert cap["scans"] == []
    assert cap["enqueued"] == []

    # Attempts 2..N: only the exhausted attempt persists terminal SKIPPED.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 2}, document_id=str(uuid.uuid4()))
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": km._SCAN_MAX_TRIES}, document_id=str(uuid.uuid4()))

    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == [{"config_id": _CFG.id, "target_revision": _BUMPED_REV}]


@pytest.mark.asyncio
async def test_clamav_error_then_clean_recovery_readds_document(monkeypatch) -> None:
    # The tradeoff of immediate SKIPPED-marking: if a later attempt recovers a CLEAN
    # verdict, the document must be re-added. After attempt 1 (error -> SKIPPED +
    # evict), prior_clean is False (the row is SKIPPED), so the recovered CLEAN
    # verdict counts as newly entering the buildable set and enqueues a re-add build.
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_error_then_clean_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=_afn(None)),
    )

    # Attempt 1: ClamAV error -> SKIPPED + one eviction rebuild.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))
    assert cap["enqueued"] == []

    # Attempt 2: scan recovers CLEAN -> re-add (entered, since the row is SKIPPED).
    result = await km.knowmap_scan_document({"job_try": 2}, document_id=str(uuid.uuid4()))
    assert result == ScanStatus.CLEAN.value
    assert len(cap["enqueued"]) == 0


@pytest.mark.asyncio
async def test_clamav_error_skipped_no_rebuild_when_never_clean(monkeypatch) -> None:
    # F-12 (W6): a never-CLEAN document that is unscannable is marked SKIPPED but
    # has no triples in the graph, so no eviction rebuild is enqueued.
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.PENDING)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_clamav_error_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=_afn(None)),
    )

    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))

    assert cap["scans"] == []
    assert cap["enqueued"] == []


def _quarantine_scanner() -> object:
    class _Scanner:
        async def scan_file(self, path: object) -> object:
            return SimpleNamespace(clean=False, threat_name="eicar")

    return _Scanner()


@pytest.mark.asyncio
async def test_quarantine_rebuilds_when_previously_clean(monkeypatch) -> None:
    # A QUARANTINED verdict on a previously-CLEAN (possibly built) document enqueues
    # a rebuild targeting a freshly bumped revision to evict its triples (F-12 W1).
    doc = _doc(size=5, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_quarantine_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=_afn(None)),
    )
    monkeypatch.setattr("shared_kernel.audit.emit", _afn(None))

    result = await km.knowmap_scan_document({}, document_id=str(uuid.uuid4()))

    assert result == ScanStatus.QUARANTINED.value
    assert cap["scans"] == [ScanStatus.QUARANTINED]
    assert cap["enqueued"] == [{"config_id": _CFG.id, "target_revision": _BUMPED_REV}]


@pytest.mark.asyncio
async def test_quarantine_no_rebuild_when_never_clean(monkeypatch) -> None:
    # F-12 (W6): a fresh (never-CLEAN) document caught as malware was never built,
    # so there are no triples to evict — no rebuild is enqueued.
    doc = _doc(size=5, scan_status=ScanStatus.PENDING)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_quarantine_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=_afn(None)),
    )
    monkeypatch.setattr("shared_kernel.audit.emit", _afn(None))

    result = await km.knowmap_scan_document({}, document_id=str(uuid.uuid4()))

    assert result == ScanStatus.QUARANTINED.value
    assert cap["scans"] == [ScanStatus.QUARANTINED]
    assert cap["enqueued"] == []


def _error_then_quarantine_scanner() -> object:
    from shared_kernel.scanning import ScanError

    class _Scanner:
        def __init__(self) -> None:
            self._calls = 0

        async def scan_file(self, path: object) -> object:
            self._calls += 1
            if self._calls == 1:
                raise ScanError("clamav down")
            return SimpleNamespace(clean=False, threat_name="eicar")

    return _Scanner()


def _error_then_clean_scanner() -> object:
    from shared_kernel.scanning import ScanError

    class _Scanner:
        def __init__(self) -> None:
            self._calls = 0

        async def scan_file(self, path: object) -> object:
            self._calls += 1
            if self._calls == 1:
                raise ScanError("clamav down")
            return SimpleNamespace(clean=True, threat_name=None)

    return _Scanner()


@pytest.mark.asyncio
async def test_clamav_error_then_quarantine_recovery_evicts_exactly_once(monkeypatch) -> None:
    # A previously-CLEAN (built) document that hits a transient ClamAV error and then
    # a real QUARANTINED verdict on retry must be evicted exactly once. The error
    # attempt marks SKIPPED and evicts on the CLEAN->SKIPPED transition; the later
    # QUARANTINED verdict is recorded but reads prior_clean=False (the row is already
    # SKIPPED), so it does not enqueue a duplicate eviction.
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_error_then_quarantine_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(download_to_path=_afn(None)),
    )
    monkeypatch.setattr("shared_kernel.audit.emit", _afn(None))

    # Attempt 1: ClamAV error -> SKIPPED + one eviction rebuild (the transition).
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))
    assert cap["enqueued"] == []

    # Attempt 2: QUARANTINED verdict recorded, but no duplicate eviction.
    result = await km.knowmap_scan_document({"job_try": 2}, document_id=str(uuid.uuid4()))

    assert result == ScanStatus.QUARANTINED.value
    assert cap["scans"] == [ScanStatus.QUARANTINED]
    assert len(cap["enqueued"]) == 1


def _afn(value: Any):
    async def _f(*args: Any, **kwargs: Any) -> Any:
        return value

    return _f
