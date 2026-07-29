"""shared_kernel.text_extraction.parsers — relocated from
contexts/knowledge/infrastructure/parsers.py (pure byte-in/str-out, no I/O)
so both the RAG ingest pipeline and chat attachment extraction can use it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import shared_kernel.text_extraction.parsers as parser_module
from shared_kernel.text_extraction.parsers import (
    MIME_TO_PARSER,
    SUPPORTED_MIMES,
    ExtractionLimits,
    ResourceBudgetError,
    normalise_mime,
    parse_markdown,
    parse_path,
    parse_path_isolated,
    parse_plaintext,
)


def test_parse_plaintext_decodes_utf8() -> None:
    assert parse_plaintext(b"hello") == "hello"


def test_parse_plaintext_replaces_invalid_sequences() -> None:
    assert parse_plaintext(b"\xff\xfe") == "��"


def test_parse_markdown_is_verbatim() -> None:
    data = b"# Title\n\nSome *text*."
    assert parse_markdown(data) == data.decode("utf-8")


def test_isolated_text_parser_returns_bounded_output(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("isolated parser", encoding="utf-8")

    assert parse_path_isolated(source, "text/plain", timeout_seconds=30) == "isolated parser"


def test_parser_tree_termination_kills_descendants_on_windows(monkeypatch) -> None:
    process = MagicMock(pid=123)
    process.is_alive.side_effect = [True, False]
    run = MagicMock()
    monkeypatch.setattr(parser_module.os, "name", "nt")
    monkeypatch.setattr(parser_module.subprocess, "run", run)

    parser_module._terminate_process_tree(process)

    run.assert_called_once_with(
        ["taskkill", "/PID", "123", "/T", "/F"],
        check=False,
        capture_output=True,
        timeout=10,
    )


def test_mime_to_parser_dispatch_table() -> None:
    assert MIME_TO_PARSER["text/plain"] is parse_plaintext
    assert MIME_TO_PARSER["text/markdown"] is parse_markdown
    assert "application/pdf" in SUPPORTED_MIMES
    assert "application/vnd.openxmlformats-officedocument.wordprocessingml.document" in SUPPORTED_MIMES


def test_path_parser_accepts_exact_utf8_budget_and_rejects_plus_one(tmp_path) -> None:
    source = tmp_path / "source.txt"
    source.write_bytes("éé".encode())
    limits = ExtractionLimits(extracted_utf8_bytes=4, estimated_tokens=10)
    assert parse_path(source, "text/plain", limits=limits) == "éé"

    source.write_bytes("ééa".encode())
    with pytest.raises(ResourceBudgetError, match="extracted_utf8_bytes"):
        parse_path(source, "text/plain", limits=limits)


def test_path_parser_accepts_exact_token_estimate_and_rejects_plus_one(tmp_path) -> None:
    source = tmp_path / "source.txt"
    limits = ExtractionLimits(extracted_utf8_bytes=100, estimated_tokens=2)
    source.write_text("abcdefgh", encoding="utf-8")
    assert parse_path(source, "text/plain", limits=limits) == "abcdefgh"

    source.write_text("abcdefghi", encoding="utf-8")
    with pytest.raises(ResourceBudgetError, match="estimated_tokens"):
        parse_path(source, "text/plain", limits=limits)


def test_docx_preflight_rejects_entry_count(tmp_path) -> None:
    import zipfile

    source = tmp_path / "source.docx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("one", "1")
        archive.writestr("two", "2")
    with pytest.raises(ResourceBudgetError, match="docx_entries"):
        parse_path(
            source,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            limits=ExtractionLimits(docx_entries=1),
        )


def test_pdf_page_budget_accepts_exact_and_rejects_plus_one(tmp_path, monkeypatch) -> None:
    import sys
    from types import SimpleNamespace

    class _Page:
        def extract_text(self) -> str:
            return "text"

    class _Reader:
        def __init__(self, path) -> None:
            self.pages = [_Page()] * int(path.read_text(encoding="ascii"))

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=_Reader))
    source = tmp_path / "source.pdf"
    source.write_text("1", encoding="ascii")
    assert parse_path(source, "application/pdf", limits=ExtractionLimits(pdf_pages=1)) == "text"

    source.write_text("2", encoding="ascii")
    with pytest.raises(ResourceBudgetError, match="pdf_pages"):
        parse_path(source, "application/pdf", limits=ExtractionLimits(pdf_pages=1))


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
