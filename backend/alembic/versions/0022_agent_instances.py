"""Agent instances — G.8 / §21.1 sub-agents.

Tracks live agent runtime instances including parent→child relationships
for sub-agents (depth = 1). Rows are retained 30 days for audit.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0022_agent_instances"
down_revision: str | Sequence[str] | None = "0021_instructions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_instances",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("parent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agent_instances.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("chatroom_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("chatrooms.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("run_context", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("task_description", sa.Text, nullable=True),
        sa.Column("state", sa.Text, nullable=False,
                  server_default=sa.text("'running'")),
        sa.Column("spawned_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("destroyed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_instances_parent_id", "agent_instances", ["parent_id"],
    )
    op.create_index(
        "ix_agent_instances_agent_id", "agent_instances", ["agent_id"],
    )
    op.create_index(
        "ix_agent_instances_destroyed_at", "agent_instances", ["destroyed_at"],
        postgresql_where=sa.text("destroyed_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_instances_destroyed_at", table_name="agent_instances")
    op.drop_index("ix_agent_instances_agent_id", table_name="agent_instances")
    op.drop_index("ix_agent_instances_parent_id", table_name="agent_instances")
    op.drop_table("agent_instances")
