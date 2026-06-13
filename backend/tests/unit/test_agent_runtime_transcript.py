"""K.2 — model-facing history assembly excludes compacted ranges (R9.10)."""

from __future__ import annotations

import uuid

import pytest

import contexts.agents.application.runtime.transcript as tx
from contexts.conversation.domain.models import Message, SenderType


def _msg(sender: SenderType, content: str, *, metadata: dict | None = None) -> Message:
    return Message(
        id=uuid.uuid4(),
        chatroom_id=uuid.uuid4(),
        sender_type=sender,
        sender_id=None,
        content_md=content,
        metadata=metadata or {},
    )


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

    class _FakeRepo:
        def __init__(self, _db) -> None:
            pass

        async def list(self, *, chatroom_id, limit=500):
            return newest_first

    monkeypatch.setattr(tx, "MessageRepository", _FakeRepo)

    history = await tx.load_model_history(object(), chatroom_id=uuid.uuid4())

    # Summary first (oldest content), then the surviving non-compacted message.
    assert [(h.role, h.content) for h in history] == [
        ("system", "SUMMARY"),
        ("user", "latest user"),
    ]
    # m1 / m2 were folded — they must not appear.
    folded = {m1.id, m2.id}
    assert all(h.id not in folded for h in history)


def test_estimate_tokens_monotonic() -> None:
    assert tx.estimate_tokens("") == 1
    assert tx.estimate_tokens("a" * 40) == 10
