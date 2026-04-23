"""Invitations + original-creator transfers.

- `invites` is polymorphic over Org / Project scope (R6.09, R6.10, §22.2a).
- `original_creator_transfers` implements the §8.5 protocol; the
  `UNIQUE (org_id) WHERE resolved_at IS NULL` predicate enforces R8.17.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003_invites"
down_revision: str | Sequence[str] | None = "0002_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("inviter_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("invitee_email", sa.Text(), nullable=False),  # citext below
        sa.Column("invitee_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("state", sa.Text(), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope_type IN ('org','project')", name="invites_scope_valid",
        ),
        sa.CheckConstraint(
            "role IN ('owner','member')", name="invites_role_valid",
        ),
        sa.CheckConstraint(
            "state IN ('pending','accepted','rejected','revoked','expired')",
            name="invites_state_valid",
        ),
    )
    op.execute(
        "ALTER TABLE invites ALTER COLUMN invitee_email TYPE CITEXT USING invitee_email::citext"
    )
    # At most one pending invite per (scope_type, scope_id, email).
    op.execute(
        "CREATE UNIQUE INDEX uq_invites_pending ON invites "
        "(scope_type, scope_id, invitee_email) WHERE state = 'pending'"
    )
    op.create_index("ix_invites_invitee_email", "invites", ["invitee_email"])
    op.create_index("ix_invites_invitee_user_id", "invites", ["invitee_user_id"])

    op.create_table(
        "original_creator_transfers",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("initiator_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("state", sa.Text(), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending','accepted','rejected','cancelled','expired','admin_forced')",
            name="oct_state_valid",
        ),
    )
    # R8.17 — at most one unresolved transfer per org.
    op.execute(
        "CREATE UNIQUE INDEX uq_oct_one_pending_per_org ON original_creator_transfers "
        "(org_id) WHERE resolved_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_oct_one_pending_per_org")
    op.drop_table("original_creator_transfers")
    op.execute("DROP INDEX IF EXISTS uq_invites_pending")
    op.drop_index("ix_invites_invitee_user_id", table_name="invites")
    op.drop_index("ix_invites_invitee_email", table_name="invites")
    op.drop_table("invites")
