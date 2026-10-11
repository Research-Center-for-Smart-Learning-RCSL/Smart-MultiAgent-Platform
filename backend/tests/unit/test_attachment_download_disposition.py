"""Serve-time hardening for chat-attachment downloads (SEC-M2, [R19a.14]).

The uploader's declared `mime` is attacker-controlled. These tests pin the
serve-time behaviour that neutralises the stored-XSS vector: scriptable types
are forced to download as a neutral octet-stream, only an allowlist of
known-safe types is served inline, and the filename can never break out of
the Content-Disposition header. Files are now served from the app origin
(`/api/files/{ticket}`), so PDF left the inline allowlist (browser-file-delivery
Q-6).
"""

from __future__ import annotations

import uuid

import pytest

from contexts.conversation.application.attachment_service import AttachmentService
from contexts.conversation.domain.models import (
    AttachmentStatus,
    MessageAttachment,
    ScanStatus,
)
from tests.unit.file_ticket_support import decode

pytestmark = pytest.mark.usefixtures("ticket_signer")


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


def _service(row: MessageAttachment) -> AttachmentService:
    svc = AttachmentService(db=None, minio=object())  # type: ignore[arg-type]
    svc._repo = _FakeRepo(row)  # type: ignore[assignment]
    return svc


async def _served(row: MessageAttachment) -> tuple[str, str]:
    ptr = await _service(row).get_for_download(attachment_id=row.id)
    ticket = await decode(ptr.url)
    assert (ticket.bucket, ticket.key) == ("chat-uploads", "proj/room/att/file.bin")
    return ticket.content_type, ticket.disposition


@pytest.mark.parametrize(
    "mime", ["text/html", "image/svg+xml", "application/xhtml+xml", "", "application/pdf"]
)
async def test_scriptable_and_document_types_forced_to_download(mime: str) -> None:
    content_type, disposition = await _served(_row(mime=mime))
    assert content_type == "application/octet-stream"
    assert disposition.startswith("attachment;")


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "text/plain"])
async def test_known_safe_types_served_inline_with_their_own_type(mime: str) -> None:
    content_type, disposition = await _served(_row(mime=mime))
    assert content_type == mime
    assert disposition.startswith("inline;")


async def test_mime_with_params_is_normalised() -> None:
    # `image/svg+xml; charset=utf-8` must still be treated as the scriptable
    # base type, not pass the allowlist by virtue of the parameter suffix.
    content_type, _ = await _served(_row(mime="image/svg+xml; charset=utf-8"))
    assert content_type == "application/octet-stream"


async def test_filename_cannot_break_out_of_header() -> None:
    _, disposition = await _served(_row(mime="image/png", filename='evil".exe\r\nX-Inject: 1'))
    assert disposition.count('"') == 2
    assert "\r" not in disposition
    assert "\n" not in disposition


async def test_cjk_filename_is_a_valid_header_value() -> None:
    _, disposition = await _served(_row(mime="image/png", filename="課堂照片.png"))
    disposition.encode("latin-1")
    assert "filename*=UTF-8''" in disposition


@pytest.mark.parametrize("mime", ["image/png", "text/plain; charset=utf-8", "application/pdf", "text/html", ""])
async def test_inline_flag_matches_what_the_link_serves(mime: str) -> None:
    """Clients decide tab-vs-download from `AttachmentOut.inline`; it must agree
    with the disposition the link actually carries."""
    from app.api.v1.attachments import to_attachment_out

    row = _row(mime=mime)
    _, disposition = await _served(row)
    assert to_attachment_out(row).inline is disposition.startswith("inline;")
