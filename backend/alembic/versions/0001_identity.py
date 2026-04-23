"""Identity schema — users, sessions, verification/reset tokens, admins, IP bans.

Implements §21.1 Identity rows + §6 lifecycle state + R6.07 60-day recovery
window (partial-unique on `users.email` keyed by `deleted_at IS NULL`).

Key IDs: R6.01–R6.08, R6.13, R5.01, §21.1 identity tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001_identity"
down_revision: str | Sequence[str] | None = "0000_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # citext is required for case-insensitive email comparison.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False),  # citext below
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("status", sa.Text(), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("banned_reason", sa.Text(), nullable=True),
        sa.Column("banned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('active','pending','banned','deleted')",
            name="users_status_valid",
        ),
    )
    # Promote email to citext after column creation so Alembic dump stays stable.
    op.execute("ALTER TABLE users ALTER COLUMN email TYPE CITEXT USING email::citext")
    # R6.07: partial-unique survives soft-delete so the recovery window can
    # hold the "old" row while a new account for the same address is forbidden.
    op.execute(
        "CREATE UNIQUE INDEX uq_users_email_active ON users (email) "
        "WHERE deleted_at IS NULL"
    )
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "ip_bans",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("cidr", pg.CIDR(), nullable=False, unique=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("banned_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    op.create_table(
        "sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_inet", pg.INET(), nullable=True),
        sa.Column("last_jti", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    for table_name in ("password_reset_tokens", "email_verify_tokens"):
        op.create_table(
            table_name,
            sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("user_id", pg.UUID(as_uuid=True),
                      sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
            sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
        )
        op.create_index(f"ix_{table_name}_user_id", table_name, ["user_id"])

    op.create_table(
        "admins",
        sa.Column("user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("promoted_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("promoted_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    op.create_table(
        "admin_impersonation_sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("admin_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("started_request_id", pg.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("admin_impersonation_sessions")
    op.drop_table("admins")
    op.drop_table("email_verify_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("ip_bans")
    op.execute("DROP INDEX IF EXISTS uq_users_email_active")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_table("users")
