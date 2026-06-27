"""Agent workspace files — designer-uploaded files for Code Interpreter.

Stores metadata for files uploaded by the agent designer that are hydrated
into the code_exec /workspace volume at turn time. MinIO is the source of
truth for the bytes; this table tracks the logical path, size, and
content-addressed key.

Revision ID: 0038_agent_workspace_files
Revises: 0037_drop_agent_mcp_servers
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0038_agent_workspace_files"
down_revision: str = "0037_drop_agent_mcp_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_workspace_files",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime", sa.Text, nullable=False),
        sa.Column("minio_key", sa.Text, nullable=False),
        sa.Column(
            "created_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_agent_workspace_files_agent_path",
        "agent_workspace_files",
        ["agent_id", "path"],
    )
    op.create_index(
        "ix_agent_workspace_files_agent",
        "agent_workspace_files",
        ["agent_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_workspace_files_agent", table_name="agent_workspace_files")
    op.drop_constraint("uq_agent_workspace_files_agent_path", "agent_workspace_files")
    op.drop_table("agent_workspace_files")
