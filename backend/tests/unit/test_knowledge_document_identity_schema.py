"""Database identity invariants for RAG and Knowledge Map documents."""

from __future__ import annotations

import sqlalchemy as sa

from contexts.knowledge.infrastructure.knowmap_tables import knowmap_documents
from contexts.knowledge.infrastructure.tables import rag_documents


def _unique_column_sets(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_rag_document_sha_is_unique_within_config() -> None:
    assert ("rag_config_id", "sha256") in _unique_column_sets(rag_documents)


def test_knowmap_document_sha_is_unique_within_config() -> None:
    assert ("knowmap_config_id", "sha256") in _unique_column_sets(knowmap_documents)


def test_document_tables_expose_processing_failure_code() -> None:
    assert rag_documents.c.failure_code.nullable
    assert knowmap_documents.c.failure_code.nullable
