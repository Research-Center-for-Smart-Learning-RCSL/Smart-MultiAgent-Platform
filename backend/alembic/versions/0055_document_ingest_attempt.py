"""rag_documents / knowmap_documents.ingest_attempt (F-23).

Large-file (tus, >32 MB) RAG and Knowledge Map ingestion enqueued the ingest
worker with a document-id-only, deterministic Arq job id
(``rag-ingest:{document_id}`` / ``knowmap-ingest:{document_id}``). Arq retains
job results for 3,600 s and deduplicates a re-enqueue of an already-known job id.
When a large-file ingest exhausted its retries and left the document ``FAILED``,
an immediate same-SHA re-upload reused the existing document row (hence the same
job id), so within the retention window Arq silently dropped the re-enqueue — no
worker ran — while the tus endpoint answered 204 as though a retry had been
scheduled.

This adds a per-document ``ingest_attempt`` counter, bumped on each genuine
finalizer re-enqueue of a terminal-non-READY document and folded into the ingest
and scan job ids, so a real retry always enqueues a fresh job while a truly
concurrent duplicate of the same attempt still dedups.

Expand-only: adding a defaulted integer column is a fast metadata-only operation
on Postgres 11+. Existing rows default ``ingest_attempt = 0``; the first
post-deploy re-upload of a failed document bumps to 1, producing a fresh job id
that enqueues — no backfill needed. Rollback drops the column and the job ids
revert to the document-only form.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055_document_ingest_attempt"
down_revision: str | Sequence[str] | None = "0054_config_delete_agent_unbind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("rag_documents", "knowmap_documents"):
        op.add_column(
            table,
            sa.Column("ingest_attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    for table in ("rag_documents", "knowmap_documents"):
        op.drop_column(table, "ingest_attempt")
