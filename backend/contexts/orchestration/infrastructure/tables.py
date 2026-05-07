"""SQLAlchemy Core tables for orchestration (G.6–G.8).

DDL owned by migrations 0020–0022. Domain layer never imports this.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

# ---------------------------------------------------------------------------
# workflow_runs (lightweight — full workflow context comes in Phase H)
# ---------------------------------------------------------------------------

workflow_runs = sa.Table(
    "workflow_runs",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "workflow_id", pg.UUID(as_uuid=True), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True
    ),
    sa.Column("trigger_type", sa.Text, nullable=True),
    sa.Column(
        "started_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'running'")),
    sa.Column("variables", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("context", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

# ---------------------------------------------------------------------------
# approvals + votes (G.6)
# ---------------------------------------------------------------------------

approvals = sa.Table(
    "approvals",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "workflow_run_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "mode",
        pg.ENUM("single", "majority", "consensus", name="approval_mode", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "leader_agent_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("approver_agent_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
    sa.Column("timeout_seconds", sa.Integer, nullable=False),
    sa.Column(
        "state",
        pg.ENUM(
            "pending", "approved", "rejected", "timeout_leader", name="approval_state", create_type=False
        ),
        nullable=False,
        server_default=sa.text("'pending'::approval_state"),
    ),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

approval_votes = sa.Table(
    "approval_votes",
    metadata,
    sa.Column(
        "approval_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("approvals.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "voter_agent_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("vote", sa.Boolean, nullable=False),
    sa.Column("rationale", sa.Text, nullable=True),
    sa.Column("cast_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.PrimaryKeyConstraint("approval_id", "voter_agent_id", name="pk_approval_votes"),
)

# ---------------------------------------------------------------------------
# instructions (G.7)
# ---------------------------------------------------------------------------

instructions = sa.Table(
    "instructions",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("chain_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("path", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
    sa.Column("depth", sa.Integer, nullable=False),
    sa.Column(
        "issuer_agent_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column(
        "target_agent_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "state",
        pg.ENUM(
            "issued",
            "delivered",
            "completed",
            "rejected_loop",
            "timeout",
            name="instruction_state",
            create_type=False,
        ),
        nullable=False,
        server_default=sa.text("'issued'::instruction_state"),
    ),
    sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

# ---------------------------------------------------------------------------
# agent_instances (G.8)
# ---------------------------------------------------------------------------

agent_instances = sa.Table(
    "agent_instances",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "parent_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agent_instances.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("run_context", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("task_description", sa.Text, nullable=True),
    sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'running'")),
    sa.Column("spawned_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("destroyed_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

__all__ = [
    "agent_instances",
    "approval_votes",
    "approvals",
    "instructions",
    "workflow_runs",
]
