"""K.2 — model-facing history assembly excludes compacted ranges (R9.10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

import contexts.agents.application.runtime.transcript as tx
from contexts.conversation.domain.models import ScanStatus
from contexts.conversation.interfaces.facade import (
    AttachmentExtractionStatus,
    AttachmentStatus,
    Message,
    MessageAttachment,
    SenderType,
)


def _msg(sender: SenderType, content: str, *, metadata: dict | None = None) -> Message:
    return Message(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        sender_type=sender,
        sender_id=None,
        content_md=content,
        metadata=metadata or {},
    )


def _attachment(
    *,
    message_id: uuid.UUID,
    status: AttachmentStatus = AttachmentStatus.ACTIVE,
    extraction_status: AttachmentExtractionStatus = AttachmentExtractionStatus.EXTRACTED,
    extracted_text: str | None = "extracted body",
    filename: str = "notes.txt",
) -> MessageAttachment:
    return MessageAttachment(
        id=uuid.uuid4(),
        message_id=message_id,
        filename=filename,
        mime="text/plain",
        size_bytes=len(extracted_text or ""),
        minio_path="chat-uploads/x",
        status=status,
        scan_status=ScanStatus.CLEAN,
        scan_at=None,
        expires_at=None,
        extracted_text=extracted_text,
        extraction_status=extraction_status,
        extracted_at=datetime.now(UTC),
    )


def _fake_facade(newest_first, attachments_by_msg=None):
    class _FakeFacade:
        def __init__(self, _db) -> None:
            pass

        async def list_messages(self, chatroom_id, *, limit=100, before_id=None):
            return newest_first

        async def list_attachments_for_messages(self, message_ids):
            return attachments_by_msg or {}

    return _FakeFacade


@pytest.mark.asyncio
async def test_load_model_history_elides_compacted_and_orders(monkeypatch) -> None:
    m1 = _msg(SenderType.USER, "first user")
    m2 = _msg(SenderType.AGENT, "first agent")
    summary = _msg(
        SenderType.SYSTEM,
        "SUMMARY",
        metadata={"type": "compact_summary", "compacted_ids": [str(m1.id), str(m2.id)]},
    )
    m3 = _msg(SenderType.USER, "latest user")
    # Repo returns newest-first; chronological is [m1, m2, summary, m3].
    newest_first = [m3, summary, m2, m1]

    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade(newest_first))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4())

    # Summary first (oldest content), then the surviving non-compacted message.
    assert [(h.role, h.content) for h in history] == [
        ("system", "SUMMARY"),
        ("user", "latest user"),
    ]
    # m1 / m2 were folded — they must not appear.
    folded = {m1.id, m2.id}
    assert all(h.id not in folded for h in history)


@pytest.mark.asyncio
async def test_load_model_history_folds_extracted_attachment_into_token_count_only(monkeypatch) -> None:
    m1 = _msg(SenderType.USER, "look at this file")
    att = _attachment(message_id=m1.id, extracted_text="A" * 100)
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4())

    assert len(history) == 1
    hm = history[0]
    # Content stays exactly the persisted text — RAG queries / the compaction
    # summariser must keep seeing today's message text unchanged.
    assert hm.content == "look at this file"
    assert hm.attachment_excerpt == "[Attached file: notes.txt]\n" + "A" * 100
    # token_count reflects content + excerpt, so compaction budgeting sees it.
    assert hm.token_count > tx.estimate_tokens("look at this file")


@pytest.mark.asyncio
async def test_load_model_history_excerpt_truncates_at_budget(monkeypatch) -> None:
    m1 = _msg(SenderType.USER, "big file")
    huge_text = "B" * (tx.HISTORY_ATTACHMENT_EXCERPT_CHARS + 500)
    att = _attachment(message_id=m1.id, extracted_text=huge_text)
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4())

    excerpt = history[0].attachment_excerpt
    assert excerpt is not None
    snippet = excerpt.removeprefix("[Attached file: notes.txt]\n")
    assert len(snippet) == tx.HISTORY_ATTACHMENT_EXCERPT_CHARS


@pytest.mark.asyncio
async def test_load_model_history_no_excerpt_when_quarantined(monkeypatch) -> None:
    # A quarantined/expired attachment must never surface, not even as a
    # placeholder -- it's not something the model should be told about.
    m1 = _msg(SenderType.USER, "a message")
    att = _attachment(
        message_id=m1.id,
        status=AttachmentStatus.QUARANTINED,
        extraction_status=AttachmentExtractionStatus.EXTRACTED,
        extracted_text="some text",
    )
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4())

    assert history[0].attachment_excerpt is None
    assert history[0].token_count == tx.estimate_tokens("a message")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "extraction_status", "extracted_text"),
    [
        (AttachmentStatus.ACTIVE, AttachmentExtractionStatus.PENDING, "some text"),
        (AttachmentStatus.ACTIVE, AttachmentExtractionStatus.FAILED, "some text"),
        (AttachmentStatus.ACTIVE, AttachmentExtractionStatus.EXTRACTED, None),
        (AttachmentStatus.ACTIVE, AttachmentExtractionStatus.UNSUPPORTED, None),
    ],
)
async def test_load_model_history_placeholder_note_when_active_but_not_extracted(
    monkeypatch, status, extraction_status, extracted_text
) -> None:
    # An active attachment with no replayable text (image, still pending,
    # failed, or an empty result) still gets a short "not available" note
    # rather than vanishing entirely -- otherwise the model has no grounding
    # to even acknowledge the file was shared, on any turn but the first.
    m1 = _msg(SenderType.USER, "a message")
    att = _attachment(
        message_id=m1.id,
        status=status,
        extraction_status=extraction_status,
        extracted_text=extracted_text,
    )
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4())

    assert history[0].attachment_excerpt == "[Attached file: notes.txt — content not available on this turn]"
    assert history[0].token_count > tx.estimate_tokens("a message")


def test_estimate_tokens_monotonic() -> None:
    assert tx.estimate_tokens("") == 1
    assert tx.estimate_tokens("a" * 40) == 10
