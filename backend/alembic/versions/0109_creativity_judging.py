"""Creativity scoring configuration and per-submission judge results.

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5
(Creativity scoring). The judging half of the spec's single 0108 (D-13).

Every column is nullable and nothing pre-0109 reads them, so old code runs on
this schema unchanged. ``judge_status`` is NULL for a submission of a type
without scoring, which is every submission that predates this migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0109_creativity_judging"
down_revision: str = "0108_activity_type_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CHECK = "ck_activity_submissions_judge_status"
_PENDING_INDEX = "ix_activity_submissions_judge_pending"


def upgrade() -> None:
    op.add_column("activity_types", sa.Column("creativity_scoring", pg.JSONB, nullable=True))
    op.add_column("activity_type_revisions", sa.Column("creativity_scoring", pg.JSONB, nullable=True))

    op.add_column("activity_submissions", sa.Column("judge_status", sa.Text, nullable=True))
    op.add_column("activity_submissions", sa.Column("judge_scores", pg.JSONB, nullable=True))
    op.add_column("activity_submissions", sa.Column("judge_reason", sa.Text, nullable=True))
    op.add_column(
        "activity_submissions", sa.Column("judged_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        _STATUS_CHECK,
        "activity_submissions",
        "judge_status IS NULL OR judge_status IN ('pending', 'scored', 'skipped', 'error')",
    )
    # The watchdog looks only for scoring still pending.
    op.create_index(
        _PENDING_INDEX,
        "activity_submissions",
        ["created_at"],
        postgresql_where=sa.text("judge_status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(_PENDING_INDEX, table_name="activity_submissions")
    op.drop_constraint(_STATUS_CHECK, "activity_submissions", type_="check")
    op.drop_column("activity_submissions", "judged_at")
    op.drop_column("activity_submissions", "judge_reason")
    op.drop_column("activity_submissions", "judge_scores")
    op.drop_column("activity_submissions", "judge_status")
    op.drop_column("activity_type_revisions", "creativity_scoring")
    op.drop_column("activity_types", "creativity_scoring")
