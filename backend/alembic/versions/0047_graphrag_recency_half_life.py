"""Concept Map recency half-life — per-config temporal decay (Phase 2b WS5, R11.21).

Adds ``recency_half_life_days DOUBLE PRECISION NULL`` to ``graphrag_configs``.
Retrieval weights each edge by ``exp(-Δt / halflife)`` over its ``last_seen_at``;
a NULL here means the config inherits the platform default
(``SMAP_GRAPHRAG_RECENCY_HALF_LIFE_DAYS_DEFAULT``, 30 days).

Expand-only and forward-compatible: old code ignores the column; the downgrade
drops it and every config reverts to pure confidence ranking.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047_graphrag_recency_half_life"
down_revision: str | Sequence[str] | None = "0046_concept_map_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "graphrag_configs",
        sa.Column("recency_half_life_days", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("graphrag_configs", "recency_half_life_days")
