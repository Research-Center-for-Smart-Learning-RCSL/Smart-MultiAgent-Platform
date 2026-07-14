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
from contexts.knowledge.domain.graphrag import BuildState
from contexts.knowledge.domain.models import DocumentStatus, ScanStatus

_CFG = SimpleNamespace(id=uuid.uuid4(), last_build_state=BuildState.IDLE, last_build_at=None)


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

    class _CfgRepo:
        def __init__(self, db: object) -> None:
            pass

        async def get(self, cfg_id: uuid.UUID) -> object:
            return _CFG

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


def _doc(*, size: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        knowmap_config_id=_CFG.id,
        size_bytes=size,
        minio_path="bucket/key",
        status=DocumentStatus.READY,
        scan_status=ScanStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_oversize_skipped_enqueues_rebuild(monkeypatch) -> None:
    # Red-first (§8.1): an over-size document is marked SKIPPED and enqueues one
    # rebuild with the config's dedup nonce — identical args to the QUARANTINED path.
    doc = _doc(size=100)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=10, scanner=SimpleNamespace())

    result = await km.knowmap_scan_document({}, document_id=str(uuid.uuid4()))

    assert result == "skipped:too_large"
    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == [
        {"config_id": _CFG.id, "last_build_state": BuildState.IDLE, "last_build_at": None}
    ]


@pytest.mark.asyncio
async def test_clamav_error_skipped_enqueues_only_on_exhausted_attempt(monkeypatch) -> None:
    from shared_kernel.scanning import ScanError

    class _Scanner:
        async def scan(self, data: object) -> object:
            raise ScanError("clamav down")

    doc = _doc(size=5)  # under the limit -> reaches the scan
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_Scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(get_object=_afn(b"data")),
    )

    # Final attempt (job_try == max_tries): mark SKIPPED, enqueue, then re-raise.
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": km._SCAN_MAX_TRIES}, document_id=str(uuid.uuid4()))

    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == [
        {"config_id": _CFG.id, "last_build_state": BuildState.IDLE, "last_build_at": None}
    ]


@pytest.mark.asyncio
async def test_clamav_error_skipped_does_not_enqueue_on_non_final_attempt(monkeypatch) -> None:
    from shared_kernel.scanning import ScanError

    class _Scanner:
        async def scan(self, data: object) -> object:
            raise ScanError("clamav down")

    doc = _doc(size=5)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_Scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(get_object=_afn(b"data")),
    )

    # A non-final attempt marks SKIPPED and re-raises WITHOUT enqueuing — the
    # document may still recover to CLEAN on retry (premature-exclusion guard, Q-2).
    with pytest.raises(ScanError):
        await km.knowmap_scan_document({"job_try": 1}, document_id=str(uuid.uuid4()))

    assert cap["scans"] == [ScanStatus.SKIPPED]
    assert cap["enqueued"] == []


@pytest.mark.asyncio
async def test_quarantine_still_enqueues_with_same_args(monkeypatch) -> None:
    # §8.4 parity: a QUARANTINED verdict still enqueues exactly as before (no
    # regression), with the same config dedup nonce a terminal SKIPPED produces.
    class _Scanner:
        async def scan(self, data: object) -> object:
            return SimpleNamespace(clean=False, threat_name="eicar")

    doc = _doc(size=5)
    cap = _install(monkeypatch, doc=doc, max_scan_bytes=1000, scanner=_Scanner())
    monkeypatch.setattr(
        "shared_kernel.storage.minio_client.get_minio_client",
        lambda: SimpleNamespace(get_object=_afn(b"data")),
    )
    monkeypatch.setattr("shared_kernel.audit.emit", _afn(None))

    result = await km.knowmap_scan_document({}, document_id=str(uuid.uuid4()))

    assert result == ScanStatus.QUARANTINED.value
    assert cap["scans"] == [ScanStatus.QUARANTINED]
    assert cap["enqueued"] == [
        {"config_id": _CFG.id, "last_build_state": BuildState.IDLE, "last_build_at": None}
    ]


def _afn(value: Any):
    async def _f(*args: Any, **kwargs: Any) -> Any:
        return value

    return _f
