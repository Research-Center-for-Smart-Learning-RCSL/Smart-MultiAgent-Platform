"""knowmap_configs.corpus_revision + built_corpus_revision (F-12).

Knowledge Map build jobs were deduplicated by an Arq job id derived from the
config's ``(last_build_state, last_build_at@1s)`` snapshot read *before* the slow
parse/chunk work and used to enqueue *after* commit. When an upload B committed
while build A was still running (or within Arq's one-hour result-retention
window), B computed the same job id A had already used, Arq dropped B's enqueue
as a duplicate, and B's documents were never built into the graph until an
unrelated later mutation.

This adds a monotonic ``corpus_revision`` — bumped in the same transaction as
every committed document mutation — so the build dedup job id keys on the actual
corpus state, and ``built_corpus_revision`` records the revision the last
successful build processed so the worker can enqueue a follow-up when the corpus
advanced during the build.

Expand-only: adding a defaulted integer column is a fast metadata-only operation
on Postgres 11+. Existing configs default ``corpus_revision = 0``; the first
post-deploy mutation bumps to 1 and enqueues a fresh job id, so no backfill is
needed. Rollback drops both columns and the job id reverts to the (state, epoch)
nonce.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0053_knowmap_corpus_revision"
down_revision: str | Sequence[str] | None = "0052_project_embedding_pins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowmap_configs",
        sa.Column("corpus_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "knowmap_configs",
        sa.Column("built_corpus_revision", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowmap_configs", "built_corpus_revision")
    op.drop_column("knowmap_configs", "corpus_revision")
