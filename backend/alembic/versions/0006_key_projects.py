"""key_projects — D.5 / §7.4.

Junction table: which Individual-owned keys are "carried into" which Project.
Withdraw is soft (``carried = false``) so the audit trail in `key_projects`
retains the original add record; the row stays unique on (key_id, project_id)
and re-carry is an upsert.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0006_key_projects"
down_revision: str | Sequence[str] | None = "0005_api_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "key_projects",
        sa.Column("key_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("carried", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("added_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("withdrawn_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key_id", "project_id", name="pk_key_projects"),
    )
    op.create_index(
        "ix_key_projects_project_carried", "key_projects",
        ["project_id"],
        postgresql_where=sa.text("carried"),
    )
    op.create_index("ix_key_projects_key", "key_projects", ["key_id"])


def downgrade() -> None:
    op.drop_index("ix_key_projects_key", table_name="key_projects")
    op.drop_index("ix_key_projects_project_carried", table_name="key_projects")
    op.drop_table("key_projects")
