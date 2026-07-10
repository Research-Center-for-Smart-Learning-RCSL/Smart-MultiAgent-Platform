"""contexts.knowledge.application.context_provider_text — shared by the
rag/graphrag/knowmap context providers, each of which used to carry its own
identical copy (code review, 2026-07-10)."""

from __future__ import annotations

from contexts.knowledge.application.context_provider_text import (
    MAX_EVIDENCE_CHARS,
    compact_excerpt,
    normalise_queries,
)


class TestNormaliseQueries:
    def test_dedups_preserving_order(self) -> None:
        assert normalise_queries(query_text="a", query_texts=["b", "a", "c"]) == ["a", "b", "c"]

    def test_whitespace_normalised(self) -> None:
        assert normalise_queries(query_text="  hi   there  ", query_texts=None) == ["hi there"]

    def test_blank_entries_dropped(self) -> None:
        assert normalise_queries(query_text=None, query_texts=["", "   ", "x"]) == ["x"]

    def test_no_input_returns_empty(self) -> None:
        assert normalise_queries(query_text=None, query_texts=None) == []


class TestCompactExcerpt:
    def test_short_text_unchanged_but_whitespace_normalised(self) -> None:
        assert compact_excerpt("  hello   world  ") == "hello world"

    def test_long_text_truncated_with_ellipsis(self) -> None:
        text = "x" * (MAX_EVIDENCE_CHARS + 50)
        out = compact_excerpt(text)
        assert len(out) == MAX_EVIDENCE_CHARS
        assert out.endswith("...")

    def test_custom_max_chars(self) -> None:
        assert compact_excerpt("hello world", max_chars=8) == "hello..."
