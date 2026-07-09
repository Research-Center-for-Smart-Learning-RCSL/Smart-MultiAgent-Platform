"""Regression: every Knowledge Map domain error resolves to its intended 4xx.

Phase 3 added the ``Knowmap*`` errors but they were initially absent from the
knowledge context's ``_MAP``; because they subclass ``KnowledgeError`` the handler
caught them, yet ``resolve_spec`` found no mapped ancestor and returned the 500
fallback. This pins each to its status so a future error is never silently a 500."""

from __future__ import annotations

import pytest

from contexts.knowledge.domain import errors
from contexts.knowledge.interfaces.error_mapping import _MAP
from shared_kernel.errors.context_handler import resolve_spec


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (errors.KnowmapConfigNotFound("x"), 404),
        (errors.KnowmapConfigNameTaken("x"), 409),
        (errors.KnowmapDocumentNotFound("x"), 404),
        (errors.KnowmapBuilderKeyGroupProjectMismatch("x"), 422),
        (errors.KnowmapNoEmbeddingKey("x"), 422),
        (errors.KnowmapEmbedDimensionConflict("x"), 422),
    ],
)
def test_knowmap_errors_map_to_intended_status(exc: Exception, expected_status: int) -> None:
    _slug, status, _title = resolve_spec(exc, _MAP)
    assert status == expected_status
    assert status != 500  # never the unmapped fallback
