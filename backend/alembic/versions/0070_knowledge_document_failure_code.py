"""Expose stable knowledge-document processing failure codes.

Revision ID: 0070_knowledge_document_failure_code
Revises: 0069_knowledge_ingest_ownership
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0070_knowledge_document_failure_code"
down_revision: str | Sequence[str] | None = "0069_knowledge_ingest_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rag_documents", sa.Column("failure_code", sa.Text(), nullable=True))
    op.add_column("knowmap_documents", sa.Column("failure_code", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowmap_documents", "failure_code")
    op.drop_column("rag_documents", "failure_code")
