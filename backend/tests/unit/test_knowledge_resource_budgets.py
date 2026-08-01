from __future__ import annotations

import pytest

from contexts.knowledge.application.resource_budgets import (
    MAX_DOCUMENT_CHUNKS,
    enforce_chunk_budget,
)
from shared_kernel.text_extraction.parsers import ResourceBudgetError


def test_chunk_budget_accepts_exact_and_rejects_plus_one() -> None:
    enforce_chunk_budget([None] * MAX_DOCUMENT_CHUNKS)

    with pytest.raises(ResourceBudgetError, match="chunks"):
        enforce_chunk_budget([None] * (MAX_DOCUMENT_CHUNKS + 1))
