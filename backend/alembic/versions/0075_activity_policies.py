"""Platform-wide activity governance policy ([R30.29]).

Revision ID: 0075_activity_policies
Revises: 0074_activity_admin_listing_indexes

One row governs the three privacy- and retention-grade `ActivityType` fields. The
table is deliberately created **empty**: `ActivityPolicyService.get_effective`
falls back to a permissive in-code default when no row exists, so applying this
migration changes no behavior until an admin saves a policy. That is what makes
the capability safe to install on a platform with existing activities.

`scope` carries a partial unique index rather than a plain unique constraint,
following `0042_prompt_studio`'s singleton-per-scope-holder shape: only one
'platform' row can exist, and a future per-org layer adds rows with a different
scope without a migration rewrite.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0075_activity_policies"
down_revision: str | Sequence[str] | None = "0074_activity_admin_listing_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_policies",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope", sa.Text, nullable=False, server_default=sa.text("'platform'")),
        sa.Column(
            "expose_payload_to_agent_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "expose_payload_to_agent_locked",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "echo_includes_content_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "echo_includes_content_locked",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("retention_days_default", sa.Integer, nullable=True),
        sa.Column("retention_days_max", sa.Integer, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "updated_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # A non-negative retention bound is a data-integrity rule, not just a
        # Pydantic one: the API is not the only writer (an operator can UPDATE).
        sa.CheckConstraint(
            "retention_days_default IS NULL OR retention_days_default > 0",
            name="ck_activity_policies_default_positive",
        ),
        sa.CheckConstraint(
            "retention_days_max IS NULL OR retention_days_max > 0",
            name="ck_activity_policies_max_positive",
        ),
        sa.CheckConstraint("scope IN ('platform')", name="ck_activity_policies_scope"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_activity_policies_platform "
        "ON activity_policies (scope) WHERE scope = 'platform'"
    )


def downgrade() -> None:
    op.drop_index("uq_activity_policies_platform", table_name="activity_policies")
    op.drop_table("activity_policies")
