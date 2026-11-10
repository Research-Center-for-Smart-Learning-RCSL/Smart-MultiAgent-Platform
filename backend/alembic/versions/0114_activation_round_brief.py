"""A round brief on each activity activation.

Spec: docs/tasks/2026-11-10-crat-lab-trial-fixes/spec.md, milestone 2
([R30.21], [R30.26]).

A nullable column nothing before 0114 reads, so old code runs on this schema
unchanged. The service normalises the brief; the CHECK only bounds its length so
a writer that bypasses the service cannot store an unbounded class-visible text.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0114_activation_round_brief"
down_revision: str = "0113_prompt_revisions_and_design_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "activity_activations"
_CHECK = "ck_activity_activations_brief_length"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("brief", sa.Text, nullable=True))
    op.create_check_constraint(_CHECK, _TABLE, "brief IS NULL OR char_length(brief) <= 200")


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "brief")
