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
    """Mirrors the real repo's *predicate*, not just its signature.

    `mark_scan` is conditional on the sha, so this double compares it too. A double that
    accepted any sha would let the stale-verdict tests below pass against the very bug
    they exist to catch.
    """

    def __init__(self, f: SkillFile | None) -> None:
        self.file = f
        # (file_id, status) pairs rather than bare statuses: the worker reads the row on
        # one repo instance and writes the verdict on another, so a bug that marked the
        # wrong id — or dropped the `uuid.UUID(file_id)` conversion — would be invisible
        # if the id were discarded here.
        self.marks: list[tuple[uuid.UUID, SkillScanStatus]] = []
        self.gets: list[uuid.UUID] = []
        # What is stored *now*. An edit moves this out from under a scan already in
        # flight against the old bytes, which is the race the sha predicate closes.
        self.current_sha = f.sha256 if f else None

    @property
    def statuses(self) -> list[SkillScanStatus]:
        return [s for _, s in self.marks]

    async def get(self, file_id: uuid.UUID) -> SkillFile | None:
        self.gets.append(file_id)
        return self.file

    async def mark_scan(
        self, file_id: uuid.UUID, *, scan_status: SkillScanStatus, expected_sha256: str
    ) -> bool:
        if expected_sha256 != self.current_sha:
            return False  # the row moved on; this verdict is about bytes that are gone
        self.marks.append((file_id, scan_status))
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
        assert w.repo.statuses == [SkillScanStatus.CLEAN]


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
        assert w.repo.statuses == [SkillScanStatus.CLEAN]
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
        assert w.repo.statuses == [SkillScanStatus.QUARANTINED]
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
        assert w.repo.statuses == [SkillScanStatus.SKIPPED]

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
        assert w.repo.statuses == [SkillScanStatus.SKIPPED]

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


class TestStaleVerdicts:
    """A verdict may only land on the bytes it was reached about.

    Found by this task's security gate. `mark_scan` originally keyed on `id` alone,
    copied from `RagDocumentRepository.mark_scan` — safe *there*, because a RAG
    document's bytes are immutable once written. Skills introduced `update_content`,
    i.e. a mutable-bytes row, into a protocol designed around immutable ones.
    """

    async def test_a_verdict_about_replaced_bytes_is_discarded(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The attack: upload 32 MiB of benign markdown, let its scan start, then PATCH the
        # file to a small malicious payload. The edit's own scan finishes first and writes
        # QUARANTINED; the slow scan of the *old* bytes then completes and would write
        # CLEAN over it. Nothing rescans, so read_skill would serve unscanned bytes
        # forever — and the attacker picks the ordering with two file sizes.
        f = _file()  # sha256 == "a"*64: the bytes this run is about
        w.repo = FakeFileRepo(f)
        w.repo.current_sha = "e" * 64  # replaced while the scan ran

        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(True)))
        monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio())

        out = await worker.skill_scan_file({}, file_id=str(uuid.uuid4()))

        assert out == "superseded"
        # The decisive assertion: no CLEAN was written. The row keeps whatever the
        # replacement's own scan leaves it, so the gate stays closed until that speaks.
        assert w.repo.marks == []

    async def test_a_stale_quarantine_is_also_discarded(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The mirror case, and why the rule is "the sha must match" rather than "never
        # overwrite CLEAN": a stale QUARANTINE about deleted bytes would brick a skill
        # whose current file is fine. Neither direction may land.
        w.repo = FakeFileRepo(_file())
        w.repo.current_sha = "e" * 64

        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr(
            "shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(False, "Eicar"))
        )
        monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio())

        assert await worker.skill_scan_file({}, file_id=str(uuid.uuid4())) == "superseded"
        assert w.repo.marks == []
        # And no audit event: naming a threat in a file whose bytes are no longer stored
        # would send an operator hunting for something that is not there.
        assert w.events == []

    async def test_a_verdict_about_the_current_bytes_still_lands(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The guard must not break the ordinary path — the failure mode of a conditional
        # write is that it silently never fires.
        w.repo = FakeFileRepo(_file())
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(True)))
        monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio())

        assert await worker.skill_scan_file({}, file_id=str(uuid.uuid4())) == "clean"
        assert w.repo.statuses == [SkillScanStatus.CLEAN]

    async def test_the_verdict_is_written_against_the_id_it_was_asked_about(
        self, w: _Wiring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The row is read on one repo instance and written on another; nothing but this
        # says the two are the same row.
        fid = uuid.uuid4()
        w.repo = FakeFileRepo(_file())
        monkeypatch.setattr(worker, "get_settings", lambda: _settings(scan_enabled=True))
        monkeypatch.setattr("shared_kernel.scanning.get_scanner", lambda: FakeScanner(FakeScanResult(True)))
        monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: FakeMinio())

        await worker.skill_scan_file({}, file_id=str(fid))

        assert w.repo.gets == [fid]
        assert w.repo.marks == [(fid, SkillScanStatus.CLEAN)]


class TestTheRepositoryPredicate:
    """The `WHERE` clause itself, compiled.

    Necessary because the tests above cannot see it: they drive `FakeFileRepo`, which
    carries its own sha comparison, so deleting the real predicate leaves them green —
    verified by probing exactly that. They pin the *worker's* half of the contract (it
    passes the scanned sha, and it discards a verdict the repo refuses); this pins the
    repository's half.

    Compiling the statement is white-box and it is the honest cheap option: the real
    proof is a concurrent write against live Postgres, which needs the `wiring` tier, and
    the value here is narrow but exact — a regression that drops the clause fails.
    """

    def test_mark_scan_filters_on_both_the_id_and_the_scanned_sha(self) -> None:
        import sqlalchemy as sa

        from contexts.skills.infrastructure import tables as t

        stmt = (
            sa.update(t.skill_files)
            .where(t.skill_files.c.id == sa.bindparam("id"), t.skill_files.c.sha256 == sa.bindparam("sha"))
            .values(scan_status="clean")
        )
        expected_where = str(stmt.compile()).split("WHERE", 1)[1]

        # The statement the repository actually builds.
        from unittest.mock import MagicMock

        from contexts.skills.infrastructure.repositories import SkillFileRepository

        captured: list[Any] = []

        class _Db:
            async def execute(self, stmt: Any) -> Any:
                captured.append(stmt)
                res = MagicMock()
                res.rowcount = 1
                return res

        import asyncio

        asyncio.run(
            SkillFileRepository(_Db()).mark_scan(  # type: ignore[arg-type]
                uuid.uuid4(), scan_status=SkillScanStatus.CLEAN, expected_sha256="a" * 64
            )
        )

        where = str(captured[0].compile()).split("WHERE", 1)[1]
        assert "sha256" in where, "the sha predicate is what stops a stale verdict landing"
        assert where.count("AND") == expected_where.count("AND")


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
