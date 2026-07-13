"""Agent sampling controls — temperature / top_p / seed (R9.18).

Adds three nullable sampling columns to ``agents`` so an agent's output can be
made low-variance and reproducible enough to audit (e.g. computing inter-rater
agreement for an LLM-judged scoring agent). All three are nullable and omitted
from the provider payload when unset, so existing agents call providers exactly
as before.

Expand-only and forward-compatible: old code ignores the columns; the downgrade
drops them and every agent reverts to the provider-default sampling behaviour.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051_agent_sampling"
down_revision: str | Sequence[str] | None = "0050_activity_activations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("temperature", sa.Float(), nullable=True))
    op.add_column("agents", sa.Column("top_p", sa.Float(), nullable=True))
    op.add_column("agents", sa.Column("seed", sa.Integer(), nullable=True))


def downgrade() -> None:
    for column in ("seed", "top_p", "temperature"):
        op.drop_column("agents", column)
