"""key_groups + key_group_members — D.6 / §7.4–§7.5.

Ordered-priority Key Groups (R7.06 — "ordered, not round-robin"). The
``UNIQUE (group_id, priority)`` constraint is DEFERRABLE so the atomic
reorder endpoint can bulk-shuffle priorities inside a single transaction
without tripping intermediate conflicts.

Backoff defaults per R7.07 are server-side column defaults so the application
never has to remember to populate them; the Alembic migration owns the
numeric values.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007_key_groups"
down_revision: str | Sequence[str] | None = "0006_key_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "key_groups",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_key_groups_project", "key_groups", ["project_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_key_groups_project_name_active "
        "ON key_groups (project_id, name) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "key_group_members",
        sa.Column("group_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("key_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("rotate_on_error_codes", pg.ARRAY(sa.Integer()),
                  nullable=False,
                  server_default=sa.text("'{429,500,502,503}'::int[]")),
        sa.Column("rotate_on_token_quota", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("retry_on_error", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("retry_initial_delay_ms", sa.Integer(), nullable=False,
                  server_default=sa.text("500")),
        sa.Column("retry_multiplier", sa.Numeric(5, 2), nullable=False,
                  server_default=sa.text("2.0")),
        sa.Column("retry_max_delay_ms", sa.Integer(), nullable=False,
                  server_default=sa.text("30000")),
        sa.Column("retry_max", sa.Integer(), nullable=False,
                  server_default=sa.text("3")),
        sa.Column("retry_jitter_pct", sa.Integer(), nullable=False,
                  server_default=sa.text("20")),
        sa.Column("max_input_tokens_per_hour", sa.BigInteger(), nullable=True),
        sa.Column("max_output_tokens_per_hour", sa.BigInteger(), nullable=True),
        sa.Column("max_requests_per_hour", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("group_id", "key_id", name="pk_key_group_members"),
        sa.CheckConstraint("priority >= 1", name="kgm_priority_pos"),
        sa.CheckConstraint("retry_jitter_pct BETWEEN 0 AND 100", name="kgm_jitter_valid"),
    )
    op.create_index(
        "ix_key_group_members_group_priority", "key_group_members",
        ["group_id", "priority"],
    )
    # Deferrable so the reorder endpoint can bulk-update priorities atomically.
    op.execute(
        "ALTER TABLE key_group_members ADD CONSTRAINT uq_key_group_members_priority "
        "UNIQUE (group_id, priority) DEFERRABLE INITIALLY IMMEDIATE"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE key_group_members DROP CONSTRAINT IF EXISTS uq_key_group_members_priority"
    )
    op.drop_index("ix_key_group_members_group_priority", table_name="key_group_members")
    op.drop_table("key_group_members")
    op.execute("DROP INDEX IF EXISTS uq_key_groups_project_name_active")
    op.drop_index("ix_key_groups_project", table_name="key_groups")
    op.drop_table("key_groups")
