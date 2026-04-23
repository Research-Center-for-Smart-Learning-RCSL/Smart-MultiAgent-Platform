"""rewrap_progress — D.10 checkpoint for resumable Transit master rotation."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0010_rewrap_progress"
down_revision: str | Sequence[str] | None = "0009_search_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rewrap_progress",
        sa.Column("table_name", sa.Text(), primary_key=True),
        sa.Column("last_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("target_transit_version", sa.Integer(), nullable=False),
        sa.Column("rows_rewrapped", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("rewrap_progress")
