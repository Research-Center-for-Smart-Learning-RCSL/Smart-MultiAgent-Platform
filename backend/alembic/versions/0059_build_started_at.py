"""build_started_at on graphrag_configs and knowmap_configs (task dossier:
docs/tasks/2026-07-17-knowmap-revision-divergence-sweep/).

``last_build_at`` cannot answer "how long has this build been running". The RUNNING
transition in ``GraphRagBuilder._run_locked`` passes neither ``built_at`` nor
``stamp_built_at``, and ``set_state`` only writes the column when given one of them, so a
config sitting in RUNNING still carries the *previous* successful build's timestamp --
possibly days old while the current build started seconds ago. Any staleness check built
on it would misfire immediately.

This adds the missing started-at watermark so a build stuck in RUNNING past every
legitimate bound (the job timeout, plus the reconciler's lock-TTL recovery latency) can be
reported. It is an observability signal only: nothing reads it to make a recovery decision.

Both tables get the column because ``set_state`` is a shared port implemented by both
repositories and the RUNNING transition lives in the shared builder -- a knowmap-only
column would force an asymmetric port. 0058 is the precedent for altering the pair
together.

Additive and nullable, so old code runs unchanged on the new schema. Rows already in
RUNNING at deploy carry NULL and are never reported, which is the intended fail-quiet
direction: a false silence costs less than a false alarm for a net that exists to catch a
broken reconciler.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0059_build_started_at"
down_revision: str | Sequence[str] | None = "0058_graphrag_build_state_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[str, ...] = ("graphrag_configs", "knowmap_configs")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("build_started_at", sa.TIMESTAMP(timezone=True), nullable=True))

    # Partial indexes for the two sweep queries. Both run once a minute over the
    # whole cross-tenant table and match very few rows, which is the shape a
    # partial index serves best; without them each tick is a seq scan that grows
    # with total config count rather than with the backlog it is there to drain.
    op.execute(
        "CREATE INDEX ix_knowmap_configs_revision_divergent ON knowmap_configs (id) "
        "WHERE deleted_at IS NULL AND last_build_state = 'idle' "
        "AND corpus_revision > COALESCE(built_corpus_revision, 0)"
    )
    op.execute(
        "CREATE INDEX ix_knowmap_configs_stale_running ON knowmap_configs (build_started_at) "
        "WHERE deleted_at IS NULL AND last_build_state = 'running' AND build_started_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowmap_configs_stale_running")
    op.execute("DROP INDEX IF EXISTS ix_knowmap_configs_revision_divergent")
    for table in _TABLES:
        op.drop_column(table, "build_started_at")
