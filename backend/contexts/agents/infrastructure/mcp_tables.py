"""SA Core table for ``mcp_egress_allowlist`` (E.9 / R12.02).

The ``agent_mcp_servers`` table already lives in ``tables.py`` (E.1) so this
module adds only the per-project hostname allowlist consulted by the Egress
Proxy. DDL is owned by ``alembic/versions/0014_mcp.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

mcp_egress_allowlist = sa.Table(
    "mcp_egress_allowlist",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("project_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("hostname", sa.Text, nullable=False),
    sa.Column("added_by_user_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
    sa.Column("note", sa.Text, nullable=True),
    sa.UniqueConstraint("project_id", "hostname",
                        name="uq_mcp_egress_allowlist_project_hostname"),
)


__all__ = ["mcp_egress_allowlist"]
