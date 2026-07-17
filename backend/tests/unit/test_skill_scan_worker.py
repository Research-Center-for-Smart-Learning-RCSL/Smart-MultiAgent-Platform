"""AC-34 / [R31.20] — the scan worker and its wiring.

The worker is mostly a mirror of `rag_scan_document`, so these tests concentrate on the
one place the mirror is deliberately imperfect: what a non-clean verdict *means*. A
`skipped` RAG document is still retrievable; a `skipped` skill file is a refusal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.workers.tasks import skills as worker
from contexts.skills.domain.models import SkillFile, SkillFileKind, SkillScanStatus
from shared_kernel import audit


class FakeScanResult:
    def __init__(self, clean: bool, threat: str | None = None) -> None:
        self.clean = clean
        self.threat_name = threat


class FakeScanner:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.scanned: list[bytes] = []

    async def scan(self, data: bytes) -> Any:
        self.scanned.append(data)
        if self._error is not None:
            raise self._error
        return self._result


class FakeFileRepo:
    def __init__(self, f: SkillFile | None) -> None:
        self.file = f
        self.marks: list[SkillScanStatus] = []

    async def get(self, file_id: uuid.UUID) -> SkillFile | None:
        return self.file

    async def mark_scan(self, file_id: uuid.UUID, *, scan_status: SkillScanStatus) -> bool:
        self.marks.append(scan_status)
        return True


class FakeMinio:
    skill_bundles_bucket = "skill-bundles"

    def __init__(self, data: bytes = b"payload") -> None:
        self.data = data

    async def get_object(self, *, bucket: str, key: str) -> bytes:
        return self.data


def _file(size_bytes: int = 10) -> SkillFile:
    return SkillFile(
        id=uuid.uuid4(),
        skill_id=uuid.uuid4(),
        path="assets/x.bin",
        kind=SkillFileKind.ASSET,
        mime="application/octet-stream",
        size_bytes=size_bytes,
        sha256="a" * 64,
        minio_key="k",
        scan_status=SkillScanStatus.PENDING,
        extracted_chars=0,
        created_at=datetime.now(UTC),
    )


class _Wiring:
    """Everything `skill_scan_file` reaches for, replaced."""

    def __init__(self) -> None:
        self.repo: FakeFileRepo | None = None
        self.events: list[audit.AuditEvent] = []


@pytest.fixture
def w(monkeypatch: pytest.MonkeyPatch) -> _Wiring:
    wiring = _Wiring()

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        def begin(self) -> _Session:
            return self

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(worker, "get_sessionmaker", lambda: (lambda: _Session()))
    monkeypatch.setattr(worker, "SkillFileRepository", lambda _db: wiring.repo)

    async def _capture(_db: object, event: audit.AuditEvent) -> None:
        wiring.events.append(event)

    monkeypatch.setattr(audit, "emit", _capture)
    return wiring


def _settings(*, scan_enabled: bool, max_bytes: int = 100 * 1024 * 1024) -> Any:
    class _Sec:
        file_scan_enabled = scan_enabled
        clamav_max_scan_bytes = max_bytes

    class _S:
        security = _Sec()

    return _S()


class TestScannerDisabled:
    async def test_no_scanner_marks_clean(self, w: _Wiring, monkeypatch: pytest.MonkeyPatch) -> None:
        # Keeps the row agreeing with `_initial_scan_status`, which already wrote CLEAN.
        w.repo = FakeFileRepo(_file())
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=False))
        out = await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))
        assert out == "clean"
        assert w.repo.marks == [SkillScanStatus.CLEAN]


class TestVerdicts:
    async def test_a_clean_file_is_marked_clean_and_not_audited(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        w.repo = FakeFileRepo(_file())
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(True)))
        monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio())

        out = await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))
        assert out == "clean"
        assert w.repo.marks == [SkillScanStatus.CLEAN]
        # R31.25 lists quarantine, not every scan — a clean verdict is not an event.
        assert w.events == []

    async def test_an_infected_file_is_quarantined_and_audited(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = _file()
        w.repo = FakeFileRepo(f)
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr(
            "shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(False, "Eicar-Test"))
        )
        monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio())

        out = await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))
        assert out == "quarantined"
        assert w.repo.marks == [SkillScanStatus.QUARANTINED]
        assert len(w.events) == 1
        assert w.events[0].action == "skill.file_quarantined"
        assert w.events[0].metadata["threat_name"] == "Eicar-Test"
        # The skill is the resource, not the file — the rest of §31's trail keys on it.
        assert w.events[0].resource_type == "skill"
        assert w.events[0].resource_id == f.skill_id

    async def test_the_bytes_scanned_are_the_stored_bytes(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        w.repo = FakeFileRepo(_file())
        scanner = FakeScanner(FakeScanResult(True))
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: scanner)
        monkeypatch.setattr(
            "shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio(b"the real bytes")
        )
        await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))
        assert scanner.scanned == [b"the real bytes"]


class TestFailurePaths:
    async def test_a_scanner_error_marks_skipped_and_reraises(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from shared_kernel.scanning import ScanError

        w.repo = FakeFileRepo(_file())
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr(
            "shared_kernel.scanning.get_scanner", lambda: FakeScanner(error=ScanError("clamd down"))
        )
        monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio())

        with pytest.raises(ScanError):
            await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))
        # Marked before the raise: the gate must be closed *during* the retries, not
        # after they are exhausted.
        assert w.repo.marks == [SkillScanStatus.SKIPPED]

    async def test_oversize_is_skipped_rather_than_passed(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Unreachable at stock settings (32 MiB cap vs 100 MiB scan limit) but reachable
        # if an operator lowers the limit. Fail-closed: we cannot vouch for bytes we did
        # not scan, so the skill stays unreadable.
        w.repo = FakeFileRepo(_file(size_bytes=200 * 1024 * 1024))
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True, max_bytes=1024))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(True)))

        out = await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))
        assert out == "skipped:too_large"
        assert w.repo.marks == [SkillScanStatus.SKIPPED]

    async def test_a_missing_file_is_not_an_error(self, w: _Wiring, monkeypatch: pytest.MonkeyPatch) -> None:
        # The row can be deleted between enqueue and run; that is not a scan failure and
        # must not burn the retries.
        w.repo = FakeFileRepo(None)
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(True)))
        out = await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))
        assert out == "not_found"
        assert w.repo.marks == []

    async def test_scan_enabled_without_a_host_is_a_hard_error(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Misconfiguration must be loud, not a silent pass — this is the branch that
        # would otherwise let a deployment believe it is scanning when it is not.
        w.repo = FakeFileRepo(_file())
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: None)
        with pytest.raises(RuntimeError, match="CLAMAV_HOST"):
            await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))


class TestWiring:
    def test_the_worker_is_registered_with_arq(self) -> None:
        # A task nobody registers never runs, and every file would sit `pending` —
        # i.e. every skill unreadable. The registration is the feature.
        from app.workers.main import WorkerSettings

        assert worker.skill_scan_file in WorkerSettings.functions

    def test_it_retries(self) -> None:
        # SKIPPED is terminal for readability, so a transient blip that goes unretried
        # leaves a legitimate skill dark.
        assert worker.skill_scan_file.max_tries == 3
