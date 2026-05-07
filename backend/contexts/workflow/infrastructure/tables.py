"""SQLAlchemy Core tables for the workflow context (H.1 / H.4).

DDL owned by migrations 0023–0024. Domain layer never imports this.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

# ---------------------------------------------------------------------------
# workflows (H.1) — versionless authoring, overwrite in place
# ---------------------------------------------------------------------------

workflows = sa.Table(
    "workflows",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "workspace_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("definition", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)
sa.Index(
    "uq_workflows_workspace_id_name",
    workflows.c.workspace_id,
    workflows.c.name,
    unique=True,
    postgresql_where=sa.text("deleted_at IS NULL"),
)

# ---------------------------------------------------------------------------
# workflow_steps (H.4) — per-node execution records
# ---------------------------------------------------------------------------

workflow_steps = sa.Table(
    "workflow_steps",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "run_id", pg.UUID(as_uuid=True), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("node_id", sa.Text, nullable=False),
    sa.Column(
        "state",
        pg.ENUM(
            "pending",
            "running",
            "succeeded",
            "failed",
            "skipped",
            "cancelled",
            name="step_state",
            create_type=False,
        ),
        nullable=False,
        server_default=sa.text("'pending'::step_state"),
    ),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("input", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("output", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("error", sa.Text, nullable=True),
)

# ---------------------------------------------------------------------------
# workflow_runs_archive (H.6 — table DDL created here, worker in Phase H.6)
# ---------------------------------------------------------------------------

workflow_runs_archive = sa.Table(
    "workflow_runs_archive",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "workflow_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("trigger_type", sa.Text, nullable=True),
    sa.Column(
        "started_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("summary", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)


__all__ = [
    "workflow_runs_archive",
    "workflow_steps",
    "workflows",
]
