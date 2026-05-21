"""Workflow runs (extend) + steps + archive — H.4 / H.6 / §21.1.

Extends the lightweight `workflow_runs` from G.6 with workflow-specific
columns. Creates `workflow_steps` and `workflow_runs_archive`.

WARNING: `downgrade()` is intentionally lossy — see the banner there.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0024_workflow_runs_steps"
down_revision: str | Sequence[str] | None = "0023_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Extend workflow_runs with H.4 columns --
    op.add_column(
        "workflow_runs",
        sa.Column("workflow_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("workflows.id", ondelete="CASCADE"),
                  nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("trigger_type", sa.Text, nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("started_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("variables", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "workflow_runs",
        sa.Column("context", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
    )
    # Rename 'status' → 'state' to match the spec enum
    op.alter_column("workflow_runs", "status", new_column_name="state")
    # Replace completed_at with ended_at
    op.alter_column("workflow_runs", "completed_at", new_column_name="ended_at")

    op.create_index(
        "ix_workflow_runs_workflow_id_started_at",
        "workflow_runs", ["workflow_id", sa.text("started_at DESC")],
    )
    op.create_index(
        "ix_workflow_runs_state_started_at",
        "workflow_runs", ["state", "started_at"],
    )

    # -- Create step_state enum --
    op.execute(
        "CREATE TYPE step_state AS ENUM "
        "('pending', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')"
    )

    # -- workflow_steps --
    op.create_table(
        "workflow_steps",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("node_id", sa.Text, nullable=False),
        sa.Column("state", pg.ENUM(
            "pending", "running", "succeeded", "failed", "skipped", "cancelled",
            name="step_state", create_type=False,
        ), nullable=False, server_default=sa.text("'pending'::step_state")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("input", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("output", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_workflow_steps_run_id_started_at",
        "workflow_steps", ["run_id", "started_at"],
    )
    op.create_index(
        "ix_workflow_steps_node_id",
        "workflow_steps", ["node_id"],
    )

    # -- workflow_runs_archive (H.6) --
    op.create_table(
        "workflow_runs_archive",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("workflows.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("trigger_type", sa.Text, nullable=True),
        sa.Column("started_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_workflow_runs_archive_workflow_id",
        "workflow_runs_archive", ["workflow_id"],
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # DESTRUCTIVE / LOSSY DOWNGRADE — DB-7
    # ------------------------------------------------------------------
    # This downgrade DROPS `workflow_steps` and `workflow_runs_archive`
    # outright and removes the `variables`, `context`, `workflow_id`,
    # `trigger_type` and `started_by_user_id` columns from `workflow_runs`.
    # Every workflow execution-history row held in those tables/columns is
    # permanently destroyed — no backup is taken here. Do NOT run this
    # against a production database unless that data has already been
    # exported elsewhere.
    # ------------------------------------------------------------------
    op.drop_index("ix_workflow_runs_archive_workflow_id",
                  table_name="workflow_runs_archive")
    op.drop_table("workflow_runs_archive")

    op.drop_index("ix_workflow_steps_node_id", table_name="workflow_steps")
    op.drop_index("ix_workflow_steps_run_id_started_at",
                  table_name="workflow_steps")
    op.drop_table("workflow_steps")

    op.execute("DROP TYPE IF EXISTS step_state")

    op.drop_index("ix_workflow_runs_state_started_at",
                  table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_workflow_id_started_at",
                  table_name="workflow_runs")

    op.alter_column("workflow_runs", "ended_at", new_column_name="completed_at")
    op.alter_column("workflow_runs", "state", new_column_name="status")

    op.drop_column("workflow_runs", "context")
    op.drop_column("workflow_runs", "variables")
    op.drop_column("workflow_runs", "started_by_user_id")
    op.drop_column("workflow_runs", "trigger_type")
    op.drop_column("workflow_runs", "workflow_id")
