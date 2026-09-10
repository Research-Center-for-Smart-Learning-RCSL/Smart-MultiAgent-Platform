"""Add canvas agent write grant and created_by_agent_id.

Phase 3 of the canvas feature ([R13.56]-[R13.58]):
- chatroom_agents.may_write_canvas: per-binding write grant
- canvas_objects.created_by_agent_id: FK to agents.id

Reversible: DROP COLUMN.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0090_canvas_agent_write"
down_revision: str | Sequence[str] | None = "0089_canvas_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chatroom_agents",
        sa.Column("may_write_canvas", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "canvas_objects",
        sa.Column("created_by_agent_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_canvas_objects_created_by_agent_id",
        "canvas_objects",
        "agents",
        ["created_by_agent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_canvas_objects_created_by_agent_id", "canvas_objects", type_="foreignkey")
    op.drop_column("canvas_objects", "created_by_agent_id")
    op.drop_column("chatroom_agents", "may_write_canvas")
