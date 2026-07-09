"""Document delta loader for the Knowledge Map build (Phase 3, WS2).

Implements the shared ``DeltaLoader`` Protocol over ``knowmap_chunks`` rather than
the conversation feed. A build reprocesses the full current ``ready`` corpus
(Non-goal: incremental delta-since-last-build, FU-1), so ``since``/``mode`` are
ignored. Windows are bounded by a token budget + a chunk cap (the Phase 2a windowing
analogue) so a large corpus keeps memory and the per-call LLM payload flat.

Each yielded unit is a :class:`DocSourceUnit`: structurally a shared ``DeltaMessage``
(so the builder types the window as ``list[DeltaMessage]`` unchanged) plus the
``document_id`` / ``chunk_idx`` the :class:`DocTripleExtractor` renders into evidence
tokens. The temporal ``created_at`` is the document's ``uploaded_at`` and is unused
downstream (a Knowledge Map is non-temporal, R11.21).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from contexts.knowledge.application.graphrag_ports import DeltaMessage
from contexts.knowledge.infrastructure.knowmap_repositories import (
    KnowmapChunkRepository,
    KnowmapDocumentRepository,
)
from shared_kernel.db.session import get_sessionmaker

# Window bounds (Phase 2a analogue): flush at whichever trips first. The token
# budget caps the per-call LLM extraction payload; the chunk cap bounds the
# per-window row count and guarantees forward progress.
_WINDOW_TOKEN_BUDGET = 24_000
_WINDOW_CHUNK_CAP = 500


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass(slots=True)
class DocSourceUnit:
    """A knowmap chunk presented to the shared builder + DocTripleExtractor."""

    id: uuid.UUID
    role: str
    content: str
    document_id: uuid.UUID
    chunk_idx: int
    created_at: datetime
    source_member_id: uuid.UUID | None = field(default=None)


class DocDeltaLoader:
    """Yield the config's ready-document chunks as bounded windows."""

    async def iter_windows(
        self,
        *,
        config_id: Any,
        since: Any,
        mode: Any,
    ) -> AsyncIterator[list[DeltaMessage]]:
        _ = (since, mode)  # full-corpus reprocess only (Non-goal: incremental)
        sm = get_sessionmaker()
        async with sm() as db:
            ready_ids = await KnowmapDocumentRepository(db).ready_document_ids(config_id=config_id)
            docs = await KnowmapDocumentRepository(db).get_many(ready_ids)
        # Stable order so evidence tokens + windows are deterministic across builds.
        docs.sort(key=lambda d: (d.uploaded_at, d.id))

        window: list[DeltaMessage] = []
        window_tokens = 0
        for doc in docs:
            async with sm() as db:
                chunks = await KnowmapChunkRepository(db).list_for_document(doc.id)
            for ch in chunks:
                tokens = _estimate_tokens(ch.text)
                if window and (
                    len(window) >= _WINDOW_CHUNK_CAP or window_tokens + tokens > _WINDOW_TOKEN_BUDGET
                ):
                    yield window
                    window = []
                    window_tokens = 0
                window.append(
                    DocSourceUnit(
                        id=uuid.uuid4(),
                        role="document",
                        content=ch.text,
                        document_id=doc.id,
                        chunk_idx=ch.chunk_idx,
                        created_at=doc.uploaded_at,
                    )
                )
                window_tokens += tokens
        if window:
            yield window


__all__ = ["DocDeltaLoader", "DocSourceUnit"]
