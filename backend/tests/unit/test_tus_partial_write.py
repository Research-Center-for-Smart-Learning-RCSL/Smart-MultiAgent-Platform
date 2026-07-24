"""Staging-file integrity across a failed TUS chunk write (R22.15.04).

The Redis offset is claimed by CAS *before* the bytes are written, so it states
intent, not fact. When a write fails part-way the two disagree, and the client
retries the same chunk on top of whatever was flushed -- producing a file that
is longer than declared with duplicated bytes wedged mid-stream, which is then
uploaded and recorded with the client-declared length as if valid.

These tests pin the three guarantees that close the gap: the failure handler
truncates back to the offset it restores, a failed truncation refuses to invite
a retry, and finalization reconciles the file against the declared length for
every purpose before any arm records a durable size.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.conversation.application import tus_service as tus_mod
from contexts.conversation.application.tus_service import TusService
from contexts.conversation.domain.errors import TusSizeMismatch
from contexts.conversation.infrastructure.tus_store import TusUpload


class _FakeStore:
    """In-memory stand-in with the real CAS semantics of TusUploadStore."""

    def __init__(self, upload: TusUpload) -> None:
        self.upload: TusUpload | None = upload
        self.deleted = False

    async def get(self, upload_id: uuid.UUID) -> TusUpload | None:
        return self.upload

    async def update_offset(self, upload_id: uuid.UUID, expected: int, new: int) -> bool:
        assert self.upload is not None
        if self.upload.upload_offset != expected:
            return False
        self.upload = replace(self.upload, upload_offset=new)
        return True

    async def delete(self, upload_id: uuid.UUID) -> None:
        self.deleted = True

    @property
    def offset(self) -> int:
        assert self.upload is not None
        return self.upload.upload_offset


def _make(
    tmp_path: object,
    *,
    upload_length: int,
    purpose: str = "chat_attachment",
) -> tuple[TusService, _FakeStore, str, uuid.UUID]:
    upload_id = uuid.uuid4()
    user_id = uuid.uuid4()
    staging_path = os.path.join(str(tmp_path), f"{upload_id}.part")
    with open(staging_path, "wb"):
        pass
    upload = TusUpload(
        upload_id=upload_id,
        user_id=user_id,
        upload_length=upload_length,
        upload_offset=0,
        purpose=purpose,
        project_id=uuid.uuid4(),
        chatroom_id=uuid.uuid4() if purpose == "chat_attachment" else None,
        rag_config_id=uuid.uuid4() if purpose == "rag_source" else None,
        knowmap_config_id=None,
        filename="doc.bin",
        mime="application/octet-stream",
        staging_path=staging_path,
        metadata_raw="",
    )
    # Patch AttachmentService out of __init__ so constructing the service does
    # not reach for a MinIO client this unit test has no use for.
    with patch("contexts.conversation.application.tus_service.AttachmentService"):
        svc = TusService(db=None)  # type: ignore[arg-type]
    store = _FakeStore(upload)
    svc._store = store  # type: ignore[assignment]
    return svc, store, staging_path, user_id


async def _patch(svc: TusService, store: _FakeStore, *, offset: int, chunk: bytes) -> None:
    assert store.upload is not None
    await svc.patch(
        upload_id=store.upload.upload_id,
        user_id=store.upload.user_id,
        offset=offset,
        chunk=chunk,
        actor_ip=None,
        request_id=None,
    )


def _partial_then_fail(prefix: int):
    """Flush *prefix* bytes of the chunk, then raise as a disk fault would."""

    def _fake(path: str, chunk: bytes) -> None:
        with open(path, "ab") as fh:
            fh.write(chunk[:prefix])
        raise OSError(28, "No space left on device")

    return _fake


# ---- truncation on a failed write ----------------------------------------- #


@pytest.mark.asyncio
async def test_a_failed_chunk_write_truncates_the_staging_file_back_to_the_prior_offset(
    tmp_path: object,
) -> None:
    svc, store, path, _ = _make(tmp_path, upload_length=60)
    await _patch(svc, store, offset=0, chunk=b"A" * 20)

    with patch.object(tus_mod, "_append_chunk", _partial_then_fail(5)), pytest.raises(OSError):
        await _patch(svc, store, offset=20, chunk=b"B" * 20)

    assert os.path.getsize(path) == 20
    assert store.offset == 20


@pytest.mark.asyncio
async def test_a_retry_after_a_failed_write_produces_a_byte_exact_file(tmp_path: object) -> None:
    svc, store, path, _ = _make(tmp_path, upload_length=60)
    await _patch(svc, store, offset=0, chunk=b"A" * 20)

    with patch.object(tus_mod, "_append_chunk", _partial_then_fail(5)), pytest.raises(OSError):
        await _patch(svc, store, offset=20, chunk=b"B" * 20)

    await _patch(svc, store, offset=20, chunk=b"B" * 20)

    with open(path, "rb") as fh:
        assert fh.read() == b"A" * 20 + b"B" * 20
    assert store.offset == 40


@pytest.mark.asyncio
async def test_a_failed_truncation_does_not_roll_the_offset_back(tmp_path: object) -> None:
    # A client that retries onto a file the server could not clean is exactly
    # how the duplicated-bytes corruption is produced, so refusing to let the
    # upload continue is the correct outcome for an unrecoverable staging file.
    svc, store, _, _ = _make(tmp_path, upload_length=60)
    await _patch(svc, store, offset=0, chunk=b"A" * 20)

    def _truncate_fails(path: str, length: int) -> None:
        raise OSError(5, "I/O error")

    with (
        patch.object(tus_mod, "_append_chunk", _partial_then_fail(5)),
        patch.object(tus_mod.os, "truncate", _truncate_fails),
        pytest.raises(OSError),
    ):
        await _patch(svc, store, offset=20, chunk=b"B" * 20)

    assert store.offset == 40


# ---- size reconciliation at finalize -------------------------------------- #


@pytest.mark.asyncio
async def test_finalize_refuses_a_staging_file_whose_size_disagrees_with_the_declared_length(
    tmp_path: object,
) -> None:
    svc, store, path, _ = _make(tmp_path, upload_length=40)
    finalize = AsyncMock()
    svc._attachments = SimpleNamespace(finalize_tus=finalize)  # type: ignore[assignment]

    await _patch(svc, store, offset=0, chunk=b"A" * 20)
    # Bytes on disk that no record accounts for -- the end state a partial write
    # followed by a retry leaves behind.
    with open(path, "ab") as fh:
        fh.write(b"X" * 5)

    with pytest.raises(TusSizeMismatch):
        await _patch(svc, store, offset=20, chunk=b"B" * 20)

    finalize.assert_not_awaited()
    # The existing `finally` still reclaims both sides of the staging state.
    assert not os.path.exists(path)
    assert store.deleted


@pytest.mark.asyncio
async def test_finalize_size_check_covers_the_rag_and_knowmap_arms_too(tmp_path: object) -> None:
    # Pins the *placement* of the check above the purpose branch: inside the
    # chat arm it would leave the rag and knowmap finalizers unguarded.
    svc, store, path, _ = _make(tmp_path, upload_length=40, purpose="rag_source")

    await _patch(svc, store, offset=0, chunk=b"A" * 20)
    with open(path, "ab") as fh:
        fh.write(b"X" * 5)

    with patch("contexts.knowledge.interfaces.facade.KnowledgeFacade") as MockFacade:
        facade = MockFacade.return_value
        facade.finalize_rag_upload = AsyncMock()
        with pytest.raises(TusSizeMismatch):
            await _patch(svc, store, offset=20, chunk=b"B" * 20)
        facade.finalize_rag_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_records_the_size_observed_on_disk(tmp_path: object) -> None:
    # A guard, not a failing test: the reconciliation above forces the observed
    # and declared sizes to be equal, so this cannot distinguish them today and
    # passed before the fix too. It exists so that a future path which drops the
    # reconciliation still has to keep the filesystem as the source of the
    # durable record rather than reverting to the client's declared length.
    svc, store, _, _ = _make(tmp_path, upload_length=40)
    finalize = AsyncMock()
    svc._attachments = SimpleNamespace(finalize_tus=finalize)  # type: ignore[assignment]

    await _patch(svc, store, offset=0, chunk=b"A" * 20)
    await _patch(svc, store, offset=20, chunk=b"B" * 20)

    assert finalize.await_args.kwargs["size_bytes"] == 40
