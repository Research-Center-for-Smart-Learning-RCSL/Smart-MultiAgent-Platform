"""Add canvas_object_comments table.

Per-object comment threads for the canvas feature ([R13.67]-[R13.70]).
Flat text comments attached to individual canvas objects, with soft delete.

Reversible: DROP TABLE.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0091_canvas_object_comments"
down_revision: str = "0090_canvas_agent_write"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canvas_object_comments",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "canvas_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("canvases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "object_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("canvas_objects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_guest_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("guest_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_canvas_object_comments_object_deleted",
        "canvas_object_comments",
        ["object_id", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_canvas_object_comments_object_deleted")
    op.drop_table("canvas_object_comments")
