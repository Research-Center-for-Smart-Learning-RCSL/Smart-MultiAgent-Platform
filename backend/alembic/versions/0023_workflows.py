"""Workflows table — H.1 / §21.1.

Versionless authoring: `version int` is an optimistic-lock counter only,
NOT a semantic version. Edits overwrite in place (R14.06 / R9.02).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0023_workflows"
down_revision: str | Sequence[str] | None = "0022_agent_instances"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("definition", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer, nullable=False,
                  server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workflows_workspace_id", "workflows", ["workspace_id"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_workflows_workspace_id_name "
        "ON workflows (workspace_id, name) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_workflows_workspace_id_name", table_name="workflows")
    op.drop_index("ix_workflows_workspace_id", table_name="workflows")
    op.drop_table("workflows")
