"""Agent system-prompt revisions and classroom design runs.

Spec: docs/tasks/2026-11-09-classroom-agent-collaboration/spec.md, milestone 4
([R34.07], [R34.08]).

Two new tables nothing before 0113 reads, so old code runs on this schema
unchanged. A design run's ``*_version_before`` columns are the versions the
owner is shown as current; apply is refused unless both agents still carry them.
At most one run per project is in flight, by a partial unique index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0113_prompt_revisions_and_design_runs"
down_revision: str = "0112_peer_reply_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNS = "classroom_design_runs"
_REVISIONS = "agent_prompt_revisions"
_REVISION_INDEX = "uq_agent_prompt_revisions_agent_version"
_RUN_INDEX = "ix_classroom_design_runs_project_created"
_IN_FLIGHT_INDEX = "uq_classroom_design_runs_in_flight"


def _agent_fk(name: str) -> sa.Column:
    return sa.Column(
        name, pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        _RUNS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        _agent_fk("design_agent_id"),
        _agent_fk("teacher_agent_id"),
        _agent_fk("peer_agent_id"),
        sa.Column("params", pg.JSONB, nullable=False),
        sa.Column("teacher_prompt_before", sa.Text, nullable=False),
        sa.Column("teacher_version_before", sa.Integer, nullable=False),
        sa.Column("peer_prompt_before", sa.Text, nullable=False),
        sa.Column("peer_version_before", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("proposed_teacher_prompt", sa.Text, nullable=True),
        sa.Column("proposed_peer_prompt", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "applied_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'applied')",
            name="ck_classroom_design_runs_status",
        ),
        sa.CheckConstraint("teacher_agent_id <> peer_agent_id", name="ck_classroom_design_runs_targets"),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'applied')) = "
            "(proposed_teacher_prompt IS NOT NULL AND proposed_peer_prompt IS NOT NULL)",
            name="ck_classroom_design_runs_proposal",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (failure_reason IS NOT NULL)", name="ck_classroom_design_runs_failure"
        ),
        sa.CheckConstraint(
            "(status = 'applied') = (applied_at IS NOT NULL)", name="ck_classroom_design_runs_applied"
        ),
        sa.CheckConstraint(
            "char_length(proposed_teacher_prompt) <= 32000 AND char_length(proposed_peer_prompt) <= 32000",
            name="ck_classroom_design_runs_prompt_length",
        ),
    )
    op.create_index(_RUN_INDEX, _RUNS, ["project_id", "created_at"])
    op.create_index(
        _IN_FLIGHT_INDEX,
        _RUNS,
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        _REVISIONS,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        _agent_fk("agent_id"),
        # The agent's version once this prompt was in place; an `initial` row
        # holds the prompt being replaced, at the version it had before the change.
        sa.Column("agent_version", sa.Integer, nullable=False),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column(
            "actor_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "design_run_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{_RUNS}.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "restored_from_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(f"{_REVISIONS}.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "source IN ('initial', 'manual', 'design', 'restore')", name="ck_agent_prompt_revisions_source"
        ),
    )
    # The version trigger makes agent_version strictly increasing per agent, so it
    # orders the history; created_at cannot, since an `initial` row and the change
    # it precedes share one transaction's now().
    op.create_index(_REVISION_INDEX, _REVISIONS, ["agent_id", "agent_version"], unique=True)


def downgrade() -> None:
    op.drop_index(_REVISION_INDEX, table_name=_REVISIONS)
    op.drop_table(_REVISIONS)
    op.drop_index(_IN_FLIGHT_INDEX, table_name=_RUNS)
    op.drop_index(_RUN_INDEX, table_name=_RUNS)
    op.drop_table(_RUNS)
