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
from types import SimpleNamespace
from typing import Any

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

        async def mark_scan(self, *, document_id, scan_status, scan_at) -> None:
            captured["scans"].append(scan_status)
            # Persist the verdict onto the row like the DB does, so a subsequent
            # arq retry that re-reads the document observes the earlier write. This
            # is what makes the multi-attempt sequence faithful (F-27 durability).
            doc.scan_status = scan_status

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
    monkeypatch.setattr("app.config.settings.get_settings", lambda: settings)
    monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: scanner)
    return captured


def _doc(*, size: int = 100, scan_status: ScanStatus = ScanStatus.PENDING) -> SimpleNamespace:
    return SimpleNamespace(
        knowmap_config_id=_CFG.id,
        size_bytes=size,
        minio_path="bucket/key",
        status=DocumentStatus.READY,
        scan_status=scan_status,
    )


def _clamav_error_scanner() -> object:
    from shared_kernel.scanning import ScanError

    class _Scanner:
        async def scan(self, data: object) -> object:
            raise ScanError("clamav down")

    return _Scanner()


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
async def test_clamav_error_persistent_evicts_previously_clean_across_retry_sequence(
    monkeypatch,
) -> None:
    # F-27 durability regression: drive the FULL arq retry sequence, not a single
    # hand-built final-attempt call. A previously-CLEAN (hence possibly built)
    # document under a persistent ClamAV error must still enqueue exactly one
    # eviction rebuild on the retry-exhausted attempt — even though each earlier
    # attempt re-reads the row the earlier attempts left behind. The old
    # single-call test (job_try=3 with the row still CLEAN) masked the bug because
    # that state is unreachable in production: by the final attempt the row is
    # already SKIPPED, which is exactly what used to destroy the ``prior_clean``
    # signal.
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_clamav_error_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(get_object=_afn(b"data")),
    )

    # Attempt 1 (job_try=1): non-final — record no terminal verdict, enqueue nothing.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))
    assert cap["enqueued"] == []
    # Attempt 2 (job_try=2): still non-final.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 2}, document_id=str(uuid.uuid4()))
    assert cap["enqueued"] == []
    # Final attempt (job_try=3): record SKIPPED and enqueue exactly one eviction
    # rebuild, because the row is still observably CLEAN from this cycle's start.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": km._SCAN_MAX_TRIES}, document_id=str(uuid.uuid4()))

    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == [{"config_id": _CFG.id, "target_revision": _BUMPED_REV}]


@pytest.mark.asyncio
async def test_clamav_error_skipped_does_not_enqueue_on_non_final_attempt(monkeypatch) -> None:
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_clamav_error_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(get_object=_afn(b"data")),
    )

    # A non-final attempt leaves the row untouched and re-raises WITHOUT enqueuing:
    # the document may still recover to CLEAN on a later retry, and writing SKIPPED
    # here would overwrite ``scan_status`` and destroy the ``prior_clean`` signal the
    # final attempt needs to decide eviction (F-27 durability, Q-2).
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))

    assert cap["scans"] == []
    assert cap["enqueued"] == []


@pytest.mark.asyncio
async def test_clamav_error_skipped_no_rebuild_when_never_clean(monkeypatch) -> None:
    # F-12 (W6): a fresh (never-CLEAN) document that is unscannable on the final
    # attempt has no triples in the graph, so no rebuild is enqueued.
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.PENDING)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_clamav_error_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(get_object=_afn(b"data")),
    )

    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": km._SCAN_MAX_TRIES}, document_id=str(uuid.uuid4()))

    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == []


def _quarantine_scanner() -> object:
    class _Scanner:
        async def scan(self, data: object) -> object:
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
        lambda: SimpleNamespace(get_object=_afn(b"data")),
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
        lambda: SimpleNamespace(get_object=_afn(b"data")),
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

        async def scan(self, data: object) -> object:
            self._calls += 1
            if self._calls == 1:
                raise ScanError("clamav down")
            return SimpleNamespace(clean=False, threat_name="eicar")

    return _Scanner()


@pytest.mark.asyncio
async def test_clamav_error_then_quarantine_recovery_evicts_previously_clean(monkeypatch) -> None:
    # A transient ClamAV error followed by a real QUARANTINED verdict on retry must
    # still evict a previously-CLEAN (built) document. This regresses the same F-27
    # durability root cause on the QUARANTINED branch: the non-final error attempt
    # must not overwrite ``scan_status`` to SKIPPED, or the recovering attempt would
    # read ``prior_clean=False`` and skip the eviction rebuild.
    from shared_kernel.scanning import ScanError

    doc = _doc(size=5, scan_status=ScanStatus.CLEAN)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_error_then_quarantine_scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(get_object=_afn(b"data")),
    )
    monkeypatch.setattr("shared_kernel.audit.emit", _afn(None))

    # Attempt 1 (job_try=1): ClamAV error, non-final — no verdict recorded, re-raise.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))
    assert cap["enqueued"] == []
    # Attempt 2 (job_try=2): scan succeeds QUARANTINED — the row is still CLEAN from
    # this cycle's start, so the eviction rebuild fires.
    result = await km.knowmap_scan_document({"job_try": 2}, document_id=str(uuid.uuid4()))

    assert result == ScanStatus.QUARANTINED.value
    assert cap["scans"] == [ScanStatus.QUARANTINED]
    assert cap["enqueued"] == [{"config_id": _CFG.id, "target_revision": _BUMPED_REV}]


def _afn(value: Any):
    async def _f(*args: Any, **kwargs: Any) -> Any:
        return value

    return _f
