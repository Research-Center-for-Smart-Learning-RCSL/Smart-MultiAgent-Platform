"""ConversationFacade attachment resolution helpers.

Covers the race-condition fix (attachments_for_message resolves against an
explicit message id instead of "whatever's latest") and the batched history
lookup used by transcript.py to replay durable extracted text.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from contexts.conversation.domain.models import (
    AttachmentExtractionStatus,
    AttachmentStatus,
    MessageAttachment,
    ScanStatus,
)
from contexts.conversation.interfaces.facade import ConversationFacade

_NOW = datetime(2026, 6, 23, 12, 0, 0)


def _attachment(
    *,
    message_id: uuid.UUID | None,
    status: AttachmentStatus = AttachmentStatus.ACTIVE,
) -> MessageAttachment:
    return MessageAttachment(
        id=uuid.uuid4(),
        message_id=message_id,
        filename="pic.png",
        mime="image/png",
        size_bytes=1024,
        minio_path="chat-uploads/key/pic.png",
        status=status,
        scan_status=ScanStatus.CLEAN,
        scan_at=_NOW,
        expires_at=_NOW + timedelta(days=3),
        extraction_status=AttachmentExtractionStatus.PENDING,
    )


def _make_facade(*, attachments: AsyncMock) -> ConversationFacade:
    facade = ConversationFacade(AsyncMock())
    facade._attachments = attachments
    return facade


async def test_attachments_for_message_filters_inactive() -> None:
    message_id = uuid.uuid4()
    active = _attachment(message_id=message_id, status=AttachmentStatus.ACTIVE)
    quarantined = _attachment(message_id=message_id, status=AttachmentStatus.QUARANTINED)
    repo = AsyncMock()
    repo.list_for_message.return_value = [active, quarantined]
    facade = _make_facade(attachments=repo)

    out = await facade.attachments_for_message(message_id)

    assert out == [active]
    repo.list_for_message.assert_awaited_once_with(message_id)


async def test_attachments_for_message_empty() -> None:
    repo = AsyncMock()
    repo.list_for_message.return_value = []
    facade = _make_facade(attachments=repo)

    assert await facade.attachments_for_message(uuid.uuid4()) == []


async def test_list_attachments_for_messages_groups_by_message_id() -> None:
    m1, m2 = uuid.uuid4(), uuid.uuid4()
    a1 = _attachment(message_id=m1)
    a2 = _attachment(message_id=m2)
    orphan = _attachment(message_id=None)  # unbound upload — must not crash grouping
    repo = AsyncMock()
    repo.list_for_messages.return_value = [a1, a2, orphan]
    facade = _make_facade(attachments=repo)

    grouped = await facade.list_attachments_for_messages([m1, m2])

    assert grouped == {m1: [a1], m2: [a2]}
