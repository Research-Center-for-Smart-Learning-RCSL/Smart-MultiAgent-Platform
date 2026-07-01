"""extract_attachment_text — best-effort chat attachment text extraction.

Unit-level coverage of the terminal outcomes (not found, unsupported mime,
parse failure, empty result, success) with fakes for the DB session, MinIO,
and the parser dispatch table — no live Postgres/Redis/MinIO required.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.workers.tasks.conversation as conv_task
import shared_kernel.text_extraction.parsers as parsers_mod
from contexts.conversation.domain.models import AttachmentExtractionStatus


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession(_NullCtx):
    def begin(self):
        return _NullCtx()


def _fake_sessionmaker():
    return lambda: _FakeSession()


def _fake_attachment_service(attachment, recorded):
    class _Service:
        def __init__(self, _session) -> None:
            pass

        async def get_by_id(self, _attachment_id):
            return attachment

        async def record_extraction_result(self, *, attachment_id, extraction_status, extracted_text):
            recorded.append((attachment_id, extraction_status, extracted_text))

    return _Service


@pytest.mark.asyncio
async def test_extract_attachment_text_not_found(monkeypatch) -> None:
    monkeypatch.setattr(conv_task, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(conv_task, "AttachmentService", _fake_attachment_service(None, []))

    out = await conv_task.extract_attachment_text({}, str(uuid.uuid4()))

    assert out == "not_found"


@pytest.mark.asyncio
async def test_extract_attachment_text_unsupported_mime(monkeypatch) -> None:
    recorded: list = []
    attachment = SimpleNamespace(mime="application/zip", minio_path="chat-uploads/x/y.zip")
    monkeypatch.setattr(conv_task, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(conv_task, "AttachmentService", _fake_attachment_service(attachment, recorded))

    out = await conv_task.extract_attachment_text({}, str(uuid.uuid4()))

    assert out == "unsupported"
    assert recorded[0][1] is AttachmentExtractionStatus.UNSUPPORTED
    assert recorded[0][2] is None


@pytest.mark.asyncio
async def test_extract_attachment_text_parse_failure(monkeypatch) -> None:
    recorded: list = []
    attachment = SimpleNamespace(mime="text/plain", minio_path="chat-uploads/x/y.txt")
    monkeypatch.setattr(conv_task, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(conv_task, "AttachmentService", _fake_attachment_service(attachment, recorded))

    def _boom(_data: bytes) -> str:
        raise parsers_mod.ParserError("nope")

    monkeypatch.setitem(parsers_mod.MIME_TO_PARSER, "text/plain", _boom)

    class _Minio:
        async def get_object(self, *, bucket, key):
            return b"irrelevant"

    monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: _Minio())

    out = await conv_task.extract_attachment_text({}, str(uuid.uuid4()))

    assert out == "failed"
    assert recorded[0][1] is AttachmentExtractionStatus.FAILED
    assert recorded[0][2] is None


@pytest.mark.asyncio
async def test_extract_attachment_text_empty_result_is_distinct_from_unsupported(monkeypatch) -> None:
    # A parser that runs cleanly but finds nothing (e.g. a blank .txt) is a
    # different outcome from "we have no parser for this mime" — it must not
    # be recorded as UNSUPPORTED, which would conflate the two causes.
    recorded: list = []
    attachment = SimpleNamespace(mime="text/plain", minio_path="chat-uploads/x/y.txt")
    monkeypatch.setattr(conv_task, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(conv_task, "AttachmentService", _fake_attachment_service(attachment, recorded))

    monkeypatch.setitem(parsers_mod.MIME_TO_PARSER, "text/plain", lambda data: "   \n\t  ")

    class _Minio:
        async def get_object(self, *, bucket, key):
            return b"irrelevant"

    monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: _Minio())

    out = await conv_task.extract_attachment_text({}, str(uuid.uuid4()))

    assert out == "empty"
    assert recorded[0][1] is AttachmentExtractionStatus.EMPTY
    assert recorded[0][2] is None


@pytest.mark.asyncio
async def test_extract_attachment_text_success_clips_to_max_chars(monkeypatch) -> None:
    recorded: list = []
    attachment = SimpleNamespace(mime="text/plain", minio_path="chat-uploads/x/y.txt")
    monkeypatch.setattr(conv_task, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(conv_task, "AttachmentService", _fake_attachment_service(attachment, recorded))
    monkeypatch.setattr(conv_task, "MAX_STORED_EXTRACTED_TEXT_CHARS", 10)

    monkeypatch.setitem(parsers_mod.MIME_TO_PARSER, "text/plain", lambda data: "A" * 50)

    class _Minio:
        async def get_object(self, *, bucket, key):
            return b"irrelevant"

    monkeypatch.setattr("shared_kernel.storage.minio_client.get_minio_client", lambda: _Minio())

    out = await conv_task.extract_attachment_text({}, str(uuid.uuid4()))

    assert out == "extracted"
    assert recorded[0][1] is AttachmentExtractionStatus.EXTRACTED
    assert recorded[0][2] == "A" * 10
