"""agent_tools.mcp_captured_tools / mcp_captured_at.

2026-07-22-mcp-tool-contract: the ``hosted_mcp`` binding stores only an
opaque ``allowed_tools`` string list in ``config`` and never negotiates a
tool contract with the upstream server, so every MCP tool is advertised to
the model with a permissive ``additionalProperties: true`` schema. This adds
a server-written column to hold the ``tools/list`` contract captured at
probe ("Test") time -- deliberately **not** folded into ``config``, since
``BoundedConfig`` (16 KB / depth 12 / 500 nodes) does not accommodate a
realistic multi-tool server and, being server-written, must never be
reachable through ``AgentToolCreateIn``/``AgentToolPatchIn``.

Pure DDL. Both columns start NULL/empty and degrade to today's permissive
schema -- population happens on the next probe, not via backfill (a backfill
would have to spin gVisor containers from inside Alembic).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0066_agent_tools_mcp_capture"
down_revision: str | Sequence[str] | None = "0065_activity_agent_visibility"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_tools",
        sa.Column("mcp_captured_tools", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "agent_tools",
        sa.Column("mcp_captured_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_tools", "mcp_captured_at")
    op.drop_column("agent_tools", "mcp_captured_tools")
