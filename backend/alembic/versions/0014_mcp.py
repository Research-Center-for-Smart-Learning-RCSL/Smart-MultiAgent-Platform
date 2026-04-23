"""mcp_egress_allowlist — E.9 / R12.02.

Per-project hostname allowlist consulted by the Egress Proxy (R12.04) for every
outbound call made from either the MCP sandbox (R12.03) or a URL-MCP (R12.06).
Unique on ``(project_id, hostname)`` so the router's "add if missing" flow is
idempotent. ``agent_mcp_servers`` already exists (0011) so this migration only
adds the allowlist table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0014_mcp"
down_revision: str | Sequence[str] | None = "0013_graphrag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_egress_allowlist",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=False),
        sa.Column("added_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.UniqueConstraint("project_id", "hostname",
                            name="uq_mcp_egress_allowlist_project_hostname"),
    )
    op.create_index(
        "ix_mcp_egress_allowlist_project",
        "mcp_egress_allowlist",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_egress_allowlist_project", table_name="mcp_egress_allowlist",
    )
    op.drop_table("mcp_egress_allowlist")
