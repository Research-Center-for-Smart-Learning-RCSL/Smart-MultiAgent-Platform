"""K.2 — model-facing history assembly excludes compacted ranges (R9.10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

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


def _room_with_one_summary(producer: uuid.UUID | None):
    """A room whose chronological history is [m1, m2, summary(m1,m2), m3]."""
    m1 = _msg(SenderType.USER, "first user")
    m2 = _msg(SenderType.AGENT, "first agent")
    meta: dict = {"type": "compact_summary", "compacted_ids": [str(m1.id), str(m2.id)]}
    if producer is not None:
        meta["producer_agent_id"] = str(producer)
    summary = _msg(SenderType.SYSTEM, "SUMMARY", metadata=meta)
    m3 = _msg(SenderType.USER, "latest user")
    return [m3, summary, m2, m1], (m1, m2, m3)  # repo returns newest-first


@pytest.mark.asyncio
async def test_load_model_history_elides_compacted_and_orders(monkeypatch) -> None:
    producer = uuid.uuid4()
    newest_first, (m1, m2, _m3) = _room_with_one_summary(producer)

    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade(newest_first))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=producer)

    # Summary first (oldest content), then the surviving non-compacted message.
    assert [(h.role, h.content) for h in history] == [
        ("system", "SUMMARY"),
        ("user", "latest user"),
    ]
    # m1 / m2 were folded — they must not appear.
    folded = {m1.id, m2.id}
    assert all(h.id not in folded for h in history)


@pytest.mark.asyncio
async def test_load_model_history_elides_only_own_agents_summary(monkeypatch) -> None:
    # R9.09: the authority to compact is per-agent, so the effect must be too.
    # A reads its own fold; B, who folded nothing, reads the whole room and does
    # NOT see A's summary text — receiving both the paraphrase and the originals
    # would be worse than the room-wide behaviour this replaces.
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    newest_first, (m1, m2, m3) = _room_with_one_summary(agent_a)
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade(newest_first))

    view_a = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=agent_a)
    view_b = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=agent_b)

    assert [(h.role, h.content) for h in view_a] == [("system", "SUMMARY"), ("user", "latest user")]
    assert [h.id for h in view_b] == [m1.id, m2.id, m3.id]
    assert all(h.content != "SUMMARY" for h in view_b)


@pytest.mark.asyncio
async def test_load_model_history_legacy_summary_without_producer(monkeypatch) -> None:
    # Q-7, the migration contract: a row written before scoping records no
    # producer, so it belongs to no one. Neither elide its range nor inject its
    # text — that restores full history to every agent rather than silently
    # preserving a room-wide fold, and a compact-mode agent simply re-folds on
    # its next turn.
    newest_first, (m1, m2, m3) = _room_with_one_summary(None)
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade(newest_first))

    view = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

    assert [h.id for h in view] == [m1.id, m2.id, m3.id]
    assert all(h.content != "SUMMARY" for h in view)


@pytest.mark.asyncio
async def test_load_model_history_treats_a_null_producer_as_no_producer(monkeypatch) -> None:
    # An explicit null must not be distinguishable from a missing key: both mean
    # "belongs to no one", and a reader whose str(uuid) somehow matched neither
    # would still be wrong.
    m1 = _msg(SenderType.USER, "folded")
    summary = _msg(
        SenderType.SYSTEM,
        "SUMMARY",
        metadata={
            "type": "compact_summary",
            "compacted_ids": [str(m1.id)],
            "producer_agent_id": None,
        },
    )
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([summary, m1]))

    view = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

    assert [h.id for h in view] == [m1.id]


@pytest.mark.asyncio
async def test_load_model_history_folds_extracted_attachment_into_token_count_only(monkeypatch) -> None:
    m1 = _msg(SenderType.USER, "look at this file")
    att = _attachment(message_id=m1.id, extracted_text="A" * 100)
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

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

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

    excerpt = history[0].attachment_excerpt
    assert excerpt is not None
    snippet = excerpt.removeprefix("[Attached file: notes.txt]\n")
    assert len(snippet) == tx.HISTORY_ATTACHMENT_EXCERPT_CHARS


@pytest.mark.asyncio
async def test_load_model_history_no_excerpt_when_quarantined(monkeypatch) -> None:
    # A quarantined attachment must never surface, not even as a placeholder --
    # it's not something the model should be told about. Expiry is treated
    # differently; see the two tests below.
    m1 = _msg(SenderType.USER, "a message")
    att = _attachment(
        message_id=m1.id,
        status=AttachmentStatus.QUARANTINED,
        extraction_status=AttachmentExtractionStatus.EXTRACTED,
        extracted_text="some text",
    )
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

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

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

    assert history[0].attachment_excerpt == "[Attached file: notes.txt — content not available on this turn]"
    assert history[0].token_count > tx.estimate_tokens("a message")


@pytest.mark.asyncio
async def test_load_model_history_still_replays_text_of_an_expired_attachment(monkeypatch) -> None:
    # `extracted_text` lives in Postgres and does not depend on the object the
    # bucket lifecycle deleted, and the model was already shown it on earlier
    # turns. Dropping it once the row flips to EXPIRED would retroactively erase
    # history the model has seen, for every attachment older than three days.
    m1 = _msg(SenderType.USER, "a message")
    att = _attachment(
        message_id=m1.id,
        status=AttachmentStatus.EXPIRED,
        extraction_status=AttachmentExtractionStatus.EXTRACTED,
        extracted_text="extracted body",
    )
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

    assert history[0].attachment_excerpt is not None
    assert "extracted body" in history[0].attachment_excerpt


@pytest.mark.asyncio
async def test_load_model_history_notes_an_expired_attachment_with_no_text_as_expired(
    monkeypatch,
) -> None:
    # An image has no replayable text, so once its bytes are gone the only
    # honest note is that it expired -- not "not available on this turn", which
    # implies it might be available on the next one.
    m1 = _msg(SenderType.USER, "a message")
    att = _attachment(
        message_id=m1.id,
        status=AttachmentStatus.EXPIRED,
        extraction_status=AttachmentExtractionStatus.UNSUPPORTED,
        extracted_text=None,
        filename="chart.png",
    )
    monkeypatch.setattr(tx, "ConversationFacade", _fake_facade([m1], {m1.id: [att]}))

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4(), for_agent_id=uuid.uuid4())

    assert history[0].attachment_excerpt == "[Attached file: chart.png — expired]"


# --------------------------------------------------------------------------- #
# Producer identity on the summary row (R9.09)
# --------------------------------------------------------------------------- #


class _RecordingFacade:
    """Captures the summary insert so the persisted metadata can be asserted."""

    seen: ClassVar[dict] = {}

    def __init__(self, _db) -> None:
        pass

    async def insert_system_message(self, *, chatroom_id, content_md, message_type, metadata=None):
        _RecordingFacade.seen = {
            "chatroom_id": chatroom_id,
            "content_md": content_md,
            "message_type": message_type,
            "metadata": dict(metadata or {}),
        }
        return _msg(SenderType.SYSTEM, content_md, metadata={**(metadata or {}), "type": message_type})


@pytest.mark.asyncio
async def test_transcript_store_records_producer_agent_id(monkeypatch) -> None:
    # The authority to compact is per-agent, so the effect must be too. Without
    # a producer on the row, the loader has nothing to scope by and every agent
    # in the room loses the folded range - including `general` agents, which
    # R9.09 says must receive the entire history.
    monkeypatch.setattr(tx, "ConversationFacade", _RecordingFacade)
    room, agent_id = uuid.uuid4(), uuid.uuid4()
    folded = [uuid.uuid4(), uuid.uuid4()]

    store = tx.MessagesTranscriptStore(object(), chatroom_id=room, agent_id=agent_id)
    await store.replace_range_with_summary(message_ids=folded, summary_text="SUMMARY")

    meta = _RecordingFacade.seen["metadata"]
    assert _RecordingFacade.seen["message_type"] == "compact_summary"
    assert meta["producer_agent_id"] == str(agent_id)
    assert meta["compacted_ids"] == [str(f) for f in folded]


def test_estimate_tokens_monotonic() -> None:
    assert tx.estimate_tokens("") == 1
    assert tx.estimate_tokens("a" * 40) == 10
