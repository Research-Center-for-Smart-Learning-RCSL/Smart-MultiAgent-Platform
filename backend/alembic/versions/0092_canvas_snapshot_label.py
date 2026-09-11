"""Add label column to canvas_snapshots.

Optional user-provided label (max 200 chars) on snapshot creation ([R13.64]).

Reversible: DROP COLUMN.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0092_canvas_snapshot_label"
down_revision: str = "0091_canvas_object_comments"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canvas_snapshots",
        sa.Column("label", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("canvas_snapshots", "label")
