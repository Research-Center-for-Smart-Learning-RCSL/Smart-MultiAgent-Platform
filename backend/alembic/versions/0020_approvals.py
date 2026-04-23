"""Approval gates — G.6 / §15.10–§15.14.

Agent-only approval workflow: approvals + approval_votes tables.
Workflow runs reference these when a node requires consensus/majority/single
approval before proceeding. Human approvers are reserved for R18.02.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0020_approvals"
down_revision: str | Sequence[str] | None = "0019_wakeup_authored_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- enums ----------------------------------------------------------------
    approval_mode = pg.ENUM(
        "single", "majority", "consensus",
        name="approval_mode", create_type=False,
    )
    op.execute("CREATE TYPE approval_mode AS ENUM ('single','majority','consensus')")

    approval_state = pg.ENUM(
        "pending", "approved", "rejected", "timeout_leader",
        name="approval_state", create_type=False,
    )
    op.execute(
        "CREATE TYPE approval_state AS ENUM "
        "('pending','approved','rejected','timeout_leader')"
    )

    # -- workflow_runs (lightweight, needed as FK target) ----------------------
    op.create_table(
        "workflow_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text, nullable=False,
                  server_default=sa.text("'running'")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # -- approvals ------------------------------------------------------------
    op.create_table(
        "approvals",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("workflow_run_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("mode", approval_mode, nullable=False),
        sa.Column("leader_agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("approver_agent_ids", pg.ARRAY(pg.UUID(as_uuid=True)),
                  nullable=False),
        sa.Column("timeout_seconds", sa.Integer, nullable=False),
        sa.Column("state", approval_state, nullable=False,
                  server_default=sa.text("'pending'::approval_state")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        "ALTER TABLE approvals ADD CONSTRAINT ck_approvals_timeout_range "
        "CHECK (timeout_seconds BETWEEN 1 AND 86400)"
    )
    op.create_index(
        "ix_approvals_workflow_run_id", "approvals", ["workflow_run_id"],
    )

    # -- approval_votes -------------------------------------------------------
    op.create_table(
        "approval_votes",
        sa.Column("approval_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("approvals.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("voter_agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("vote", sa.Boolean, nullable=False),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("cast_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("approval_id", "voter_agent_id",
                                name="pk_approval_votes"),
    )


def downgrade() -> None:
    op.drop_table("approval_votes")
    op.drop_table("approvals")
    op.drop_table("workflow_runs")
    op.execute("DROP TYPE IF EXISTS approval_state")
    op.execute("DROP TYPE IF EXISTS approval_mode")
