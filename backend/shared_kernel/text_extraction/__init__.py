"""Pure document text extraction (pdf/docx/md/txt) shared across contexts."""

from __future__ import annotations

from shared_kernel.text_extraction.parsers import (
    MIME_TO_PARSER,
    SUPPORTED_MIMES,
    ExtractionLimits,
    ParserError,
    ResourceBudgetError,
    parse_docx,
    parse_markdown,
    parse_path,
    parse_pdf,
    parse_plaintext,
)

__all__ = [
    "MIME_TO_PARSER",
    "SUPPORTED_MIMES",
    "ExtractionLimits",
    "ParserError",
    "ResourceBudgetError",
    "parse_path",
    "parse_docx",
    "parse_markdown",
    "parse_pdf",
    "parse_plaintext",
]
