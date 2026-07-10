"""Pure text helpers shared by the three Axis-1/context providers
(:mod:`rag_context_provider`, :mod:`graphrag_context_provider`,
:mod:`knowmap_context_provider`).

Each provider used to carry its own identical copy of query normalization
(and, for the two graph providers, evidence-excerpt compaction) — collapsed
here (code review, 2026-07-10).
"""

from __future__ import annotations

from collections.abc import Sequence

MAX_EVIDENCE_CHARS = 280


def normalise_queries(*, query_text: str | None, query_texts: Sequence[str] | None) -> list[str]:
    """Whitespace-normalize and dedup the turn's query text(s), in order."""
    queries: list[str] = []
    for raw in ([query_text] if query_text is not None else []) + list(query_texts or []):
        text = " ".join(str(raw or "").split())
        if text and text not in queries:
            queries.append(text)
    return queries


def compact_excerpt(text: str, *, max_chars: int = MAX_EVIDENCE_CHARS) -> str:
    """Whitespace-normalize and truncate an evidence excerpt to ``max_chars``."""
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


__all__ = ["MAX_EVIDENCE_CHARS", "compact_excerpt", "normalise_queries"]
