"""Unit tests for the prompt_studio reference-file service (§29 / AC-7).

DB-free: a fake config repo + fake MinIO storage + monkeypatched scanner drive
the format / size / budget / infected / happy paths without Postgres or MinIO.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

import contexts.prompt_studio.application.file_service as file_mod
from contexts.prompt_studio.application.file_service import FileService
from contexts.prompt_studio.domain.errors import (
    ExtractedTextBudgetExceeded,
    FileFormatUnsupported,
    FileInfected,
    FileTooLarge,
)
from contexts.prompt_studio.domain.models import (
    EXTRACTED_TEXT_BUDGET,
    FILE_MAX_BYTES,
    AssistantFile,
    ScanStatus,
)
from shared_kernel.scanning import ScanError, ScanResult

_NOW = datetime(2026, 7, 5, tzinfo=UTC)


class _FakeConfigRepo:
    def __init__(self, existing_chars: int = 0) -> None:
        self.existing_chars = existing_chars
        self.added: list[dict] = []

    async def sum_extracted_chars(self, config_id, *, only_clean=True):
        return self.existing_chars

    async def add_file(self, *, config_id, values):
        self.added.append(values)
        return AssistantFile(
            id=uuid.uuid4(),
            config_id=config_id,
            filename=values["filename"],
            size_bytes=values["size_bytes"],
            sha256=values["sha256"],
            mime=values["mime"],
            minio_key=values["minio_key"],
            scan_status=ScanStatus(values["scan_status"]),
            extracted_chars=values["extracted_chars"],
            extracted_text=values["extracted_text"],
            created_at=_NOW,
        )


class _FakeStorage:
    prompt_assistant_files_bucket = "prompt-assistant-files"

    def __init__(self) -> None:
        self.put_calls: list[tuple] = []

    async def put_object(self, *, bucket, key, data, content_type):
        self.put_calls.append((bucket, key, len(data)))

    async def remove(self, *, bucket, key):
        return None


class _Scanner:
    def __init__(self, *, clean=True, raise_error=False):
        self._clean = clean
        self._raise = raise_error

    async def scan(self, data):
        if self._raise:
            raise ScanError("clamd unreachable")
        return ScanResult(clean=self._clean, threat_name=None if self._clean else "EICAR")


def _make_service(*, repo, storage=None, scanner=None, monkeypatch) -> FileService:
    async def _noop_emit(*_a, **_k):
        return None

    monkeypatch.setattr(file_mod.audit, "emit", _noop_emit)
    monkeypatch.setattr(file_mod, "get_scanner", lambda: scanner)
    svc = FileService.__new__(FileService)
    svc._db = object()
    svc._storage = storage or _FakeStorage()
    svc._configs = repo
    return svc


@pytest.mark.asyncio
async def test_rejects_unsupported_format(monkeypatch) -> None:
    svc = _make_service(repo=_FakeConfigRepo(), monkeypatch=monkeypatch)
    with pytest.raises(FileFormatUnsupported):
        await svc.upload_reference_file(
            config_id=uuid.uuid4(), filename="evil.exe", data=b"x", mime="x", actor_user_id=uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_rejects_oversize(monkeypatch) -> None:
    svc = _make_service(repo=_FakeConfigRepo(), monkeypatch=monkeypatch)
    with pytest.raises(FileTooLarge):
        await svc.upload_reference_file(
            config_id=uuid.uuid4(),
            filename="big.txt",
            data=b"a" * (FILE_MAX_BYTES + 1),
            mime="text/plain",
            actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_rejects_infected(monkeypatch) -> None:
    svc = _make_service(repo=_FakeConfigRepo(), scanner=_Scanner(clean=False), monkeypatch=monkeypatch)
    with pytest.raises(FileInfected):
        await svc.upload_reference_file(
            config_id=uuid.uuid4(),
            filename="doc.txt",
            data=b"hello",
            mime="text/plain",
            actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_fails_closed_when_scan_unavailable(monkeypatch) -> None:
    svc = _make_service(repo=_FakeConfigRepo(), scanner=_Scanner(raise_error=True), monkeypatch=monkeypatch)
    with pytest.raises(FileInfected):
        await svc.upload_reference_file(
            config_id=uuid.uuid4(),
            filename="doc.txt",
            data=b"hello",
            mime="text/plain",
            actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_rejects_over_budget(monkeypatch) -> None:
    # Config already near the budget; a new file's text tips it over.
    repo = _FakeConfigRepo(existing_chars=EXTRACTED_TEXT_BUDGET - 2)
    svc = _make_service(repo=repo, monkeypatch=monkeypatch)  # no scanner configured
    with pytest.raises(ExtractedTextBudgetExceeded):
        await svc.upload_reference_file(
            config_id=uuid.uuid4(),
            filename="doc.txt",
            data=b"hello world",  # 11 chars > remaining budget of 2
            mime="text/plain",
            actor_user_id=uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_happy_path_stores_extracted_text(monkeypatch) -> None:
    repo = _FakeConfigRepo(existing_chars=0)
    storage = _FakeStorage()
    svc = _make_service(repo=repo, storage=storage, scanner=_Scanner(clean=True), monkeypatch=monkeypatch)
    file = await svc.upload_reference_file(
        config_id=uuid.uuid4(),
        filename="Style Guide.txt",
        data=b"secret answer is 42",
        mime="text/plain",
        actor_user_id=uuid.uuid4(),
    )
    assert file.scan_status is ScanStatus.CLEAN
    assert file.extracted_text == "secret answer is 42"
    assert file.extracted_chars == len("secret answer is 42")
    assert len(storage.put_calls) == 1
    assert repo.added[0]["scan_status"] == ScanStatus.CLEAN.value
