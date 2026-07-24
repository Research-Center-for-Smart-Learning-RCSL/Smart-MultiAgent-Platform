"""Model-facing transcript: history assembly + compaction store (K.2).

The compaction logic in ``contexts.agents.application.context`` is pure and
depends on two Protocols — ``TranscriptStore`` and ``MessageLike``. This module
provides their production implementations over the conversation context's
message repository.

Design (R9.10): the user-visible transcript is **never** mutated. Compaction
inserts a ``system`` message tagged ``{"type": "compact_summary",
"compacted_ids": [...], "producer_agent_id": "..."}``; the *model-facing*
history loader then omits any message id listed in a summary's
``compacted_ids`` while keeping the summary itself. Because compaction always
folds the oldest un-compacted range, every summary represents strictly older
content than any surviving message — so the model-facing order is
``[summaries…oldest-first] + [survivors…chronological]``.

Scope (R9.09): ``context_mode`` is an **agent** setting, so a fold is scoped to
the agent that produced it. The loader takes a reader and applies only that
reader's own summaries — eliding their range and injecting their text. Another
agent's summary is applied to neither, so an agent configured ``general`` keeps
receiving the entire history no matter what any other agent compacted. Summary
rows written before this scoping carry no producer and belong to no one: they
are neither applied nor injected, which restores full history rather than
silently preserving a room-wide fold. That per-producer scoping is also what
makes the "strictly older content" invariant above true — it holds within one
producer's view, which is the only view that ever exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.runtime.model_attachments import format_attachment_text
from contexts.conversation.interfaces.facade import (
    AttachmentExtractionStatus,
    AttachmentStatus,
    ConversationFacade,
    Message,
    MessageAttachment,
    SenderType,
)

# Canonical definition now lives in the shared kernel so the knowledge providers
# can size their blocks against the same estimate the context manager budgets
# with (F-16). Re-exported so existing ``transcript.estimate_tokens`` imports hold.
from shared_kernel.tokens import estimate_tokens

# How many recent rows to pull when assembling history. Compaction keeps the
# live window bounded; this is the safety ceiling on a single turn's scan.
DEFAULT_HISTORY_WINDOW = 500

# Bounded per-message replay of an older attachment's extracted text. This
# repeats on every turn for every surviving historical row that has one
# (unlike the live vision/PDF blocks, which only render once), so it is kept
# deliberately small relative to the live-turn inlining budget.
HISTORY_ATTACHMENT_EXCERPT_CHARS = 4_000

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
    sender_id: uuid.UUID | None
    role: str
    content: str
    metadata: dict[str, Any]
    token_count: int
    # Bounded replay text for an older message's extracted attachment content
    # (None when the message has no replayable attachment, or when it's the
    # turn's own triggering message — that one carries live content blocks
    # instead, spliced in by the turn engine).
    attachment_excerpt: str | None = None


def _attachment_excerpt(attachments: Sequence[MessageAttachment] | None) -> str | None:
    if not attachments:
        return None
    parts: list[str] = []
    budget = HISTORY_ATTACHMENT_EXCERPT_CHARS
    for a in attachments:
        # Quarantine is a scan verdict: the file is dangerous and must not be
        # described to the model at all. Expiry is not the same judgement --
        # `extracted_text` lives in Postgres and does not depend on the object
        # the bucket lifecycle deleted, and the model was already shown it on
        # earlier turns, so dropping it once the row flips to EXPIRED would
        # retroactively erase history the model has seen for every attachment
        # older than the TTL.
        if a.status is AttachmentStatus.QUARANTINED:
            continue
        if a.extraction_status is AttachmentExtractionStatus.EXTRACTED and a.extracted_text:
            if budget <= 0:
                continue
            snippet = a.extracted_text[:budget]
            parts.append(format_attachment_text(a.filename, snippet))
            budget -= len(snippet)
        else:
            # No text to replay (image/other non-extractable mime, still
            # pending, or extraction failed) -- note the file's existence
            # rather than going silent. Without this, an attachment type that
            # can never durably replay (images) becomes permanently invisible
            # the moment it's no longer the triggering message, and the model
            # has no grounding to even acknowledge it was shared. "expired" is
            # distinguished from "not available on this turn" because the
            # latter implies it might be available on the next one.
            reason = (
                "expired" if a.status is AttachmentStatus.EXPIRED else "content not available on this turn"
            )
            parts.append(f"[Attached file: {a.filename} — {reason}]")
    return "\n\n".join(parts) if parts else None


def _is_summary(metadata: Any) -> bool:
    return isinstance(metadata, dict) and metadata.get("type") == _COMPACT_TYPE


def _summary_producer(metadata: Any) -> str | None:
    """The agent a summary belongs to, or None for a pre-scoping row.

    A missing key and an explicit null are the same answer — belongs to no one —
    so neither can match a reader.
    """
    if not isinstance(metadata, dict):
        return None
    producer = metadata.get("producer_agent_id")
    return str(producer) if producer else None


def _to_history(msg: Message, attachments: Sequence[MessageAttachment] | None = None) -> HistoryMessage:
    role = "system" if _is_summary(msg.metadata) else _ROLE_BY_SENDER.get(msg.sender_type, "user")
    # Excerpt only for user rows — an agent/system row never carries an
    # uploaded attachment. Folded into token_count (so compaction budgeting
    # stays accurate) but deliberately NOT into `.content`, so RAG query
    # building and the compaction summariser keep seeing exactly today's
    # message text; only the token budget and the final provider-message
    # rendering (turn_engine.py) change.
    #
    # Trade-off: `choose_range_to_compact` (context.py) walks oldest-first and
    # accumulates `token_count` until it meets its shed target, so a message
    # whose excerpt makes it token-heavy satisfies that target sooner — it (and
    # its excerpt) is more likely to be folded into a summary before an
    # excerpt-free message would be. This is intentional: the excerpt is real,
    # recurring token weight sent to the model on every surviving turn, so
    # compaction shedding it first is compaction working as designed (bound
    # the live window by *actual* size), not a bug — the alternative
    # (excluding the excerpt from token_count) would let a room's real context
    # size silently exceed `context_token_cap` without `should_compact` ever
    # noticing.
    excerpt = _attachment_excerpt(attachments) if role == "user" else None
    counted_text = f"{msg.content_md}\n\n{excerpt}" if excerpt else msg.content_md
    return HistoryMessage(
        id=msg.id,
        sender_id=msg.sender_id,
        role=role,
        content=msg.content_md,
        metadata=dict(msg.metadata or {}),
        token_count=estimate_tokens(counted_text),
        attachment_excerpt=excerpt,
    )


async def load_model_history(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    for_agent_id: uuid.UUID,
    window: int = DEFAULT_HISTORY_WINDOW,
) -> list[HistoryMessage]:
    """Build ``for_agent_id``'s model-facing history for ``chatroom_id``.

    Only summaries produced by ``for_agent_id`` are applied (R9.09): theirs
    elide their range and contribute their text; every other summary — another
    agent's, or a pre-scoping row with no producer — is applied to neither, so
    its folded messages are returned intact.
    """
    facade = ConversationFacade(db)
    rows = await facade.list_messages(chatroom_id, limit=window)
    chronological = list(reversed(rows))  # repo returns newest-first
    attachments_by_msg = await facade.list_attachments_for_messages([m.id for m in chronological])

    reader = str(for_agent_id)

    def _is_own_summary(metadata: Any) -> bool:
        return _is_summary(metadata) and _summary_producer(metadata) == reader

    compacted: set[uuid.UUID] = set()
    for m in chronological:
        if _is_own_summary(m.metadata):
            for cid in m.metadata.get("compacted_ids") or []:
                try:
                    compacted.add(uuid.UUID(str(cid)))
                except (ValueError, AttributeError):
                    continue

    summaries: list[HistoryMessage] = []
    survivors: list[HistoryMessage] = []
    for m in chronological:
        if _is_summary(m.metadata):
            # The elision set and this list must use the same predicate: admit a
            # foreign summary's text here and the reader would get both the
            # paraphrase and the originals, which is worse than the room-wide
            # behaviour this replaces.
            if _is_own_summary(m.metadata):
                summaries.append(_to_history(m))
        elif m.id not in compacted:
            survivors.append(_to_history(m, attachments_by_msg.get(m.id)))
    return summaries + survivors


class MessagesTranscriptStore:
    """Production ``TranscriptStore`` — folds a range into a summary row.

    ``agent_id`` is the *producing* agent and is mandatory: the summary is a
    projection over that agent's model-facing view only (R9.09), and a row that
    does not record its producer cannot be scoped by the loader.
    """

    def __init__(self, db: AsyncSession, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> None:
        self._facade = ConversationFacade(db)
        self._chatroom_id = chatroom_id
        self._agent_id = agent_id

    async def replace_range_with_summary(
        self,
        *,
        message_ids: list[Any],
        summary_text: str,
    ) -> uuid.UUID:
        # `sender_id` stays NULL: the row is a service-owned system message, not
        # a message *from* the agent, and the user-visible feed renders it as a
        # system divider. The producer lives in metadata, which the conversation
        # context stamps server-side and no client can supply.
        msg = await self._facade.insert_system_message(
            chatroom_id=self._chatroom_id,
            content_md=summary_text,
            message_type=_COMPACT_TYPE,
            metadata={
                "compacted_ids": [str(m) for m in message_ids],
                "producer_agent_id": str(self._agent_id),
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
