"""workflow_run_participants — A2A rule 3a authorization source (G.2).

Materialized at run start from the workflow definition's agent references.
A2A rule 3a grants an A2A/instruct between two agents only when both are
participants of the same run, so this table is the authorization surface.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0062_workflow_run_participants"
down_revision: str | Sequence[str] | None = "0061_graphrag_owner_index_live_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_run_participants",
        sa.Column(
            "run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # PK (run_id, agent_id) serves both the point lookup ("is X a participant
        # of run R") and the prefix scan ("participants of run R"); no extra index.
        sa.PrimaryKeyConstraint("run_id", "agent_id"),
    )


def downgrade() -> None:
    op.drop_table("workflow_run_participants")
