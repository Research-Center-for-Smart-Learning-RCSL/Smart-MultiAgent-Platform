"""shared_kernel.text_extraction.parsers — relocated from
contexts/knowledge/infrastructure/parsers.py (pure byte-in/str-out, no I/O)
so both the RAG ingest pipeline and chat attachment extraction can use it.
"""

from __future__ import annotations

from shared_kernel.text_extraction.parsers import (
    MIME_TO_PARSER,
    SUPPORTED_MIMES,
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
