"""Hard resource ceilings shared by knowledge ingestion families."""

from __future__ import annotations

from collections.abc import Sized

from shared_kernel.text_extraction.parsers import ResourceBudgetError

MAX_DOCUMENT_CHUNKS = 20_000


def enforce_chunk_budget(chunks: Sized) -> None:
    if len(chunks) > MAX_DOCUMENT_CHUNKS:
        raise ResourceBudgetError("chunks")


__all__ = ["MAX_DOCUMENT_CHUNKS", "enforce_chunk_budget"]
