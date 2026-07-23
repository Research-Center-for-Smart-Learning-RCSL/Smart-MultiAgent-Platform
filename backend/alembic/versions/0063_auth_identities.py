"""auth_identities + nullable users.password_hash — Google OAuth login (§6.1a).

Adds the provider-identity table backing R6.14-R6.17 ("Sign in with Google")
and makes `users.password_hash` nullable so a Google-only account can exist
without a password. `email` mirrors the `users.email` convention: stored citext
so a later lookup stays case-insensitive, though it is an informational,
last-seen snapshot only — resolution keys on `(provider, provider_subject)`.

Key IDs: R6.14, R6.15, R6.16, R6.17.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0063_auth_identities"
down_revision: str | Sequence[str] | None = "0062_workflow_run_participants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A Google-only account has no password; password login is one credential
    # among several (R6.15). Old code stays forward-compatible: it only ever
    # inserted a non-null hash, and every read tolerates NULL after this task.
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)

    op.create_table(
        "auth_identities",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),  # citext below
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        # One external account maps to exactly one SMAP user (R6.17).
        sa.UniqueConstraint("provider", "provider_subject", name="uq_auth_identities_provider_subject"),
        # A user links at most one account per provider (R6.17).
        sa.UniqueConstraint("user_id", "provider", name="uq_auth_identities_user_provider"),
    )
    # Match the users.email convention (Text column promoted to citext in-DB).
    op.execute("ALTER TABLE auth_identities ALTER COLUMN email TYPE CITEXT USING email::citext")
    # No standalone (user_id) index: the uq_auth_identities_user_provider unique
    # index is btree-backed on (user_id, provider), so its leading column already
    # serves user_id-prefix scans (same rationale as 0062_workflow_run_participants).


def downgrade() -> None:
    op.drop_table("auth_identities")
    # Re-tightening password_hash to NOT NULL would fail on any Google-only
    # (NULL-hash) account created while this migration was live. Backfill those
    # rows with an unusable, non-Argon2 sentinel so the row survives (the owner
    # must reset their password) and the NOT NULL can be restored without loss.
    op.execute("UPDATE users SET password_hash = '!disabled!' WHERE password_hash IS NULL")
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)
