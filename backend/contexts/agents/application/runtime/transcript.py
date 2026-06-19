"""Model-facing transcript: history assembly + compaction store (K.2).

The compaction logic in ``contexts.agents.application.context`` is pure and
depends on two Protocols — ``TranscriptStore`` and ``MessageLike``. This module
provides their production implementations over the conversation context's
message repository.

Design (R9.10): the user-visible transcript is **never** mutated. Compaction
inserts a ``system`` message tagged ``{"type": "compact_summary",
"compacted_ids": [...]}``; the *model-facing* history loader then omits any
message id listed in a summary's ``compacted_ids`` while keeping the summary
itself. Because compaction always folds the oldest un-compacted range, every
summary represents strictly older content than any surviving message — so the
model-facing order is ``[summaries…oldest-first] + [survivors…chronological]``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.interfaces.facade import ConversationFacade, Message, SenderType

# How many recent rows to pull when assembling history. Compaction keeps the
# live window bounded; this is the safety ceiling on a single turn's scan.
DEFAULT_HISTORY_WINDOW = 500

_COMPACT_TYPE = "compact_summary"
_ROLE_BY_SENDER = {
    SenderType.USER: "user",
    SenderType.AGENT: "agent",
    SenderType.SYSTEM: "system",
}


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    """Concrete ``MessageLike`` (see ``context.MessageLike``)."""

    id: uuid.UUID
    role: str
    content: str
    metadata: dict[str, Any]
    token_count: int


def estimate_tokens(text: str) -> int:
    """Coarse token estimate; CJK characters count as 1 token each, Latin as
    len//4 (M10). Messages carry no token column."""
    cjk = 0
    latin = 0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
                or 0x3040 <= cp <= 0x30FF or 0xAC00 <= cp <= 0xD7AF
                or 0xFF00 <= cp <= 0xFFEF):
            cjk += 1
        else:
            latin += 1
    return max(1, cjk + latin // 4)


def _is_summary(metadata: Any) -> bool:
    return isinstance(metadata, dict) and metadata.get("type") == _COMPACT_TYPE


def _to_history(msg: Message) -> HistoryMessage:
    role = "system" if _is_summary(msg.metadata) else _ROLE_BY_SENDER.get(msg.sender_type, "user")
    return HistoryMessage(
        id=msg.id,
        role=role,
        content=msg.content_md,
        metadata=dict(msg.metadata or {}),
        token_count=estimate_tokens(msg.content_md),
    )


async def load_model_history(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    window: int = DEFAULT_HISTORY_WINDOW,
) -> list[HistoryMessage]:
    """Build the model-facing history for ``chatroom_id`` (compacted ranges elided)."""
    rows = await ConversationFacade(db).list_messages(chatroom_id, limit=window)
    chronological = list(reversed(rows))  # repo returns newest-first

    compacted: set[uuid.UUID] = set()
    for m in chronological:
        if _is_summary(m.metadata):
            for cid in m.metadata.get("compacted_ids") or []:
                try:
                    compacted.add(uuid.UUID(str(cid)))
                except (ValueError, AttributeError):
                    continue

    summaries: list[HistoryMessage] = []
    survivors: list[HistoryMessage] = []
    for m in chronological:
        if _is_summary(m.metadata):
            summaries.append(_to_history(m))
        elif m.id not in compacted:
            survivors.append(_to_history(m))
    return summaries + survivors


class MessagesTranscriptStore:
    """Production ``TranscriptStore`` — folds a range into a summary row."""

    def __init__(self, db: AsyncSession, *, chatroom_id: uuid.UUID) -> None:
        self._facade = ConversationFacade(db)
        self._chatroom_id = chatroom_id

    async def replace_range_with_summary(
        self,
        *,
        message_ids: list[Any],
        summary_text: str,
    ) -> uuid.UUID:
        msg = await self._facade.create_message(
            chatroom_id=self._chatroom_id,
            sender_type=SenderType.SYSTEM,
            sender_id=None,
            content_md=summary_text,
            metadata={
                "type": _COMPACT_TYPE,
                "compacted_ids": [str(m) for m in message_ids],
            },
        )
        return msg.id


__all__ = [
    "DEFAULT_HISTORY_WINDOW",
    "HistoryMessage",
    "MessagesTranscriptStore",
    "estimate_tokens",
    "load_model_history",
]
