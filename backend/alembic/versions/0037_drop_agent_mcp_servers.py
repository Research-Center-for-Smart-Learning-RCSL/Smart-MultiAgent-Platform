"""Drop agent_mcp_servers — replaced by agent_tools (0036).

Code cutover is complete: all routes, services, and the turn engine now
read/write ``agent_tools``. The old table and its ``agent_mcp_source`` enum
are no longer referenced by any runtime code.

Down migration re-creates the table and enum but does NOT attempt to backfill
data from ``agent_tools`` — a production downgrade past this point requires
restoring from backup.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0037_drop_agent_mcp_servers"
down_revision: str | Sequence[str] | None = "0036_agent_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MCP_SOURCES: tuple[str, ...] = ("builtin", "url", "package")


def upgrade() -> None:
    op.drop_index("ix_agent_mcp_servers_agent", table_name="agent_mcp_servers")
    op.drop_table("agent_mcp_servers")
    op.execute("DROP TYPE agent_mcp_source")


def downgrade() -> None:
    op.execute(
        "CREATE TYPE agent_mcp_source AS ENUM ("
        + ", ".join(f"'{v}'" for v in _MCP_SOURCES)
        + ")"
    )
    op.create_table(
        "agent_mcp_servers",
        sa.Column(
            "id", pg.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "source",
            pg.ENUM(*_MCP_SOURCES, name="agent_mcp_source", create_type=False),
            nullable=False,
        ),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column(
            "allowed_tools", pg.ARRAY(sa.Text()), nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "config", pg.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_agent_mcp_servers_agent", "agent_mcp_servers", ["agent_id"])
