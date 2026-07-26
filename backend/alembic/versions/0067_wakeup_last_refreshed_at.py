"""Add the durable per-agent wake-up refresh clock."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0067_wakeup_last_refreshed_at"
down_revision: str | Sequence[str] | None = "0066_agent_tools_mcp_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "wakeup_last_refreshed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "wakeup_last_refreshed_at")
