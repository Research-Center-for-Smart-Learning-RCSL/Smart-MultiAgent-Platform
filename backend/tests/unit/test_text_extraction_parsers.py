"""shared_kernel.text_extraction.parsers — relocated from
contexts/knowledge/infrastructure/parsers.py (pure byte-in/str-out, no I/O)
so both the RAG ingest pipeline and chat attachment extraction can use it.
"""

from __future__ import annotations

from shared_kernel.text_extraction.parsers import (
    MIME_TO_PARSER,
    SUPPORTED_MIMES,
    normalise_mime,
    parse_markdown,
    parse_plaintext,
)


def test_parse_plaintext_decodes_utf8() -> None:
    assert parse_plaintext(b"hello") == "hello"


def test_parse_plaintext_replaces_invalid_sequences() -> None:
    assert parse_plaintext(b"\xff\xfe") == "��"


def test_parse_markdown_is_verbatim() -> None:
    data = b"# Title\n\nSome *text*."
    assert parse_markdown(data) == data.decode("utf-8")


def test_mime_to_parser_dispatch_table() -> None:
    assert MIME_TO_PARSER["text/plain"] is parse_plaintext
    assert MIME_TO_PARSER["text/markdown"] is parse_markdown
    assert "application/pdf" in SUPPORTED_MIMES
    assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in SUPPORTED_MIMES


class TestNormaliseMime:
    """Shared by RAG and Knowledge Map ingest (used to be two identical
    copies — collapsed here in code review, 2026-07-10)."""

    def test_strips_parameters(self) -> None:
        assert normalise_mime("text/plain; charset=utf-8", "f.txt") == "text/plain"

    def test_falls_back_to_filename(self) -> None:
        assert normalise_mime("application/octet-stream", "doc.pdf") == "application/pdf"

    def test_preserves_valid_mime(self) -> None:
        assert normalise_mime("text/markdown", "f.md") == "text/markdown"

    def test_empty_falls_back(self) -> None:
        assert normalise_mime("", "f.txt") == "text/plain"
