"""GraphRAG embed-model pin — expand-only nullable columns (Phase 2a D2, R11.18).

Pins one ``(provider, model, dimension)`` per project so every GraphRAG config in
a project embeds into the shared per-project Qdrant collection at a single, stable
vector dimension. Before this, the embedding model — and therefore the vector
dimension — was derived at build time from whichever key sorted first in the
builder key group, so swapping the group's first embedding provider silently
changed the dimension and mis-indexed against the fixed-size collection.

The three columns are nullable so every pre-Phase-2a row stays legal: the config
service resolves and persists the triple on create/update, and a legacy null-pin
row self-pins on its next successful build. Expand-only — old code ignores the new
columns, so this is forward-compatible; the downgrade simply drops them and the
pin logic degrades to the pre-2a derive-from-key behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045_graphrag_embed_pin"
down_revision: str | Sequence[str] | None = "0044_graphrag_drop_agent_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("graphrag_configs", sa.Column("embed_provider", sa.Text(), nullable=True))
    op.add_column("graphrag_configs", sa.Column("embed_model", sa.Text(), nullable=True))
    op.add_column("graphrag_configs", sa.Column("embed_dim", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("graphrag_configs", "embed_dim")
    op.drop_column("graphrag_configs", "embed_model")
    op.drop_column("graphrag_configs", "embed_provider")
