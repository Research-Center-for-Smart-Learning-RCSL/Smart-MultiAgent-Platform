"""Audit-log backbone with append-only DB trigger (R17.04).

The trigger blocks UPDATE / DELETE unless the session role matches
`smap_audit_retention`; the nightly retention worker authenticates as that
role to run its scheduled `DELETE` against rows older than 365 days (R17.01).

Running this migration creates the retention role in a NOLOGIN /
NOINHERIT shape so granting it to the app user is a deliberate `SET ROLE`
step inside the retention worker only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004_audit"
down_revision: str | Sequence[str] | None = "0003_invites"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_ip", pg.INET(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_audit_logs_actor_user_id_created_at",
        "audit_logs",
        ["actor_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_logs_resource",
        "audit_logs",
        ["resource_type", "resource_id"],
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # The retention role exists only so the trigger has something definite to
    # check against. DO NOT grant it to the backend AppRole.
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'smap_audit_retention') THEN
            CREATE ROLE smap_audit_retention NOLOGIN NOINHERIT;
          END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION smap_audit_logs_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF current_user = 'smap_audit_retention' THEN
            RETURN COALESCE(NEW, OLD);
          END IF;
          RAISE EXCEPTION 'audit_logs is append-only (R17.04)'
            USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    # Row-level trigger covers UPDATE + DELETE. TRUNCATE is statement-level,
    # so it needs its own trigger with a separate function (the shared one
    # dereferences NEW/OLD which are NULL under TRUNCATE).
    op.execute(
        "CREATE TRIGGER trg_audit_logs_append_only "
        "BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION smap_audit_logs_append_only();"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION smap_audit_logs_no_truncate()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF current_user = 'smap_audit_retention' THEN
            RETURN NULL;
          END IF;
          RAISE EXCEPTION 'audit_logs is append-only (R17.04) — TRUNCATE denied'
            USING ERRCODE = 'insufficient_privilege';
        END
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_logs_no_truncate "
        "BEFORE TRUNCATE ON audit_logs "
        "FOR EACH STATEMENT EXECUTE FUNCTION smap_audit_logs_no_truncate();"
    )

    # Runtime-adjustable rate-limit policy table (R19.04).
    op.create_table(
        "rate_limit_policies",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("window_sec", sa.Integer(), nullable=False),
        sa.Column("max_count", sa.Integer(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "scope IN ('user','ip','user_and_ip')",
            name="rlp_scope_valid",
        ),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_policies")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON audit_logs;")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs;")
    op.execute("DROP FUNCTION IF EXISTS smap_audit_logs_no_truncate();")
    op.execute("DROP FUNCTION IF EXISTS smap_audit_logs_append_only();")
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    # We intentionally do NOT drop the role on downgrade — other installs may
    # share the same cluster and it is harmless if unused.
