"""search_keys — D.11 / §12.4.

Mirrors `api_keys` (envelope + masked + test-status) with two differences:
  * Owned by a Project, not an Individual.
  * Partial-unique-active index guarantees at most one active key per project.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009_search_keys"
down_revision: str | Sequence[str] | None = "0008_key_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROVIDERS: tuple[str, ...] = ("brave", "serper", "tavily", "google_cse")


def upgrade() -> None:
    op.execute(
        "CREATE TYPE search_provider AS ENUM ("
        + ", ".join(f"'{v}'" for v in _PROVIDERS)
        + ")"
    )

    op.create_table(
        "search_keys",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider",
                  pg.ENUM(*_PROVIDERS, name="search_provider", create_type=False),
                  nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("dek_wrapped", sa.Text(), nullable=False),
        sa.Column("ciphertext_hmac", sa.LargeBinary(), nullable=False),
        sa.Column("transit_key_version", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("hmac_key_version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("masked_preview", sa.Text(), nullable=False),
        sa.Column(
            "test_status",
            pg.ENUM("ok", "failed", "untested", name="key_test_status", create_type=False),
            nullable=False,
            server_default=sa.text("'untested'::key_test_status"),
        ),
        sa.Column("test_error", sa.Text(), nullable=True),
        sa.Column("last_test_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("config", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_search_keys_project", "search_keys", ["project_id"])
    # At most one active, non-deleted key per project (§12.4 R12.09).
    op.execute(
        "CREATE UNIQUE INDEX uq_search_keys_active_per_project "
        "ON search_keys (project_id) WHERE is_active AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_search_keys_active_per_project")
    op.drop_index("ix_search_keys_project", table_name="search_keys")
    op.drop_table("search_keys")
    op.execute("DROP TYPE search_provider")
