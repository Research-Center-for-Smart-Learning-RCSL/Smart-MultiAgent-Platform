"""Serve-time hardening for chat-attachment downloads (SEC-M2).

The uploader's declared `mime` is attacker-controlled and stored verbatim on
the object. These tests pin the serve-time behaviour that neutralises the
stored-XSS vector: scriptable types are forced to download as a neutral
octet-stream, only an allowlist of known-safe types is served inline, and the
filename can never break out of the Content-Disposition header.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from contexts.conversation.application.attachment_service import AttachmentService
from contexts.conversation.domain.models import (
    AttachmentStatus,
    MessageAttachment,
    ScanStatus,
)


class _FakeMinio:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def presigned_get(
        self,
        *,
        bucket: str,
        key: str,
        expires: timedelta = timedelta(minutes=15),
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "bucket": bucket,
                "key": key,
                "content_type": response_content_type,
                "disposition": response_content_disposition,
            }
        )
        return f"https://minio.local/{bucket}/{key}?signed=1"


class _FakeRepo:
    def __init__(self, row: MessageAttachment) -> None:
        self._row = row

    async def get(self, attachment_id: uuid.UUID) -> MessageAttachment | None:
        return self._row if attachment_id == self._row.id else None


def _row(*, mime: str, filename: str = "file.bin") -> MessageAttachment:
    return MessageAttachment(
        id=uuid.uuid4(),
        message_id=None,
        filename=filename,
        mime=mime,
        size_bytes=10,
        minio_path="chat-uploads/proj/room/att/file.bin",
        status=AttachmentStatus.ACTIVE,
        scan_status=ScanStatus.CLEAN,
        scan_at=None,
        expires_at=None,
    )


def _service(row: MessageAttachment) -> tuple[AttachmentService, _FakeMinio]:
    minio = _FakeMinio()
    svc = AttachmentService(db=None, minio=minio)  # type: ignore[arg-type]
    svc._repo = _FakeRepo(row)  # type: ignore[assignment]
    return svc, minio


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "application/xhtml+xml", ""])
async def test_scriptable_types_forced_to_download(mime: str) -> None:
    row = _row(mime=mime)
    svc, minio = _service(row)
    await svc.get_for_download(attachment_id=row.id)
    call = minio.calls[0]
    assert call["content_type"] == "application/octet-stream"
    assert str(call["disposition"]).startswith("attachment;")


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "application/pdf", "text/plain"])
async def test_known_safe_types_served_inline_with_their_own_type(mime: str) -> None:
    row = _row(mime=mime)
    svc, minio = _service(row)
    await svc.get_for_download(attachment_id=row.id)
    call = minio.calls[0]
    assert call["content_type"] == mime
    assert str(call["disposition"]).startswith("inline;")


@pytest.mark.asyncio
async def test_mime_with_params_is_normalised() -> None:
    # `image/svg+xml; charset=utf-8` must still be treated as the scriptable
    # base type, not pass the allowlist by virtue of the parameter suffix.
    row = _row(mime="image/svg+xml; charset=utf-8")
    svc, minio = _service(row)
    await svc.get_for_download(attachment_id=row.id)
    assert minio.calls[0]["content_type"] == "application/octet-stream"


@pytest.mark.asyncio
async def test_filename_cannot_break_out_of_header() -> None:
    # Quotes / CRLF in the filename must be stripped so they cannot inject
    # additional Content-Disposition parameters.
    row = _row(mime="image/png", filename='evil".exe\r\nX-Inject: 1')
    svc, minio = _service(row)
    await svc.get_for_download(attachment_id=row.id)
    disposition = str(minio.calls[0]["disposition"])
    # Exactly the opening + closing quote of the filename value — no injected
    # quote could close it early — and no CRLF to start a new header.
    assert disposition.count('"') == 2
    assert "\r" not in disposition
    assert "\n" not in disposition
