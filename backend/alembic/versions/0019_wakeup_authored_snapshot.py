"""Add wakeup_authored_snapshot to agents — G.5 periodic refresh.

The ``wakeup_authored_snapshot`` column stores the designer's most recent
human edit of ``wakeup_config``. The periodic refresh job (G.5) resets
``wakeup_config`` back to this snapshot every ``refresh_every_hours``.

Because there is no agent versioning (R9.02), "authored values" is the
most recent human edit captured in this companion column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0019_wakeup_authored_snapshot"
down_revision: str | Sequence[str] | None = "0018_attachments_chatroom"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "wakeup_authored_snapshot",
            pg.JSONB,
            nullable=True,
            comment="Designer's authored wakeup_config; G.5 refresh target",
        ),
    )
    # Backfill: copy current wakeup_config as the initial snapshot.
    op.execute(
        "UPDATE agents SET wakeup_authored_snapshot = wakeup_config "
        "WHERE wakeup_config != '{}'::jsonb"
    )


def downgrade() -> None:
    op.drop_column("agents", "wakeup_authored_snapshot")
