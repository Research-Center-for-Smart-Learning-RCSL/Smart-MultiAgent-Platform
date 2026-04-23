"""api_keys + enums — D.4 / §7.2.

Creates:
  * `api_key_provider` enum (claude/openai/gemini/voyage/cohere) — R7.01.
  * `key_test_status` enum (ok/failed/untested).
  * `api_keys` table — envelope-encrypted secret, owned by an Individual, no
    plaintext column ever (R7.15).

Indexes follow `D.4 §Schema`:
  * `(owner_user_id, provider)` — list-own-keys hot path.
  * `(provider)` — feeds D.10 rotation walks.

SoC: this migration touches only DDL. Capability fitness (R7.01) is enforced
at application layer in `contexts.keys.domain.providers.assert_capability`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005_api_keys"
down_revision: str | Sequence[str] | None = "0004_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PROVIDER_VALUES: tuple[str, ...] = ("claude", "openai", "gemini", "voyage", "cohere")
_STATUS_VALUES: tuple[str, ...] = ("ok", "failed", "untested")


def upgrade() -> None:
    op.execute(
        "CREATE TYPE api_key_provider AS ENUM ("
        + ", ".join(f"'{v}'" for v in _PROVIDER_VALUES)
        + ")"
    )
    op.execute(
        "CREATE TYPE key_test_status AS ENUM ("
        + ", ".join(f"'{v}'" for v in _STATUS_VALUES)
        + ")"
    )

    op.create_table(
        "api_keys",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        # Always an Individual — never an org_id. `key_projects` handles
        # "carried into project" (R7.04 / D.5).
        sa.Column("owner_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "provider",
            pg.ENUM(*_PROVIDER_VALUES, name="api_key_provider", create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        # Envelope fields — §7.6. `dek_wrapped` is `vault:vN:...` so its Transit
        # version is recoverable without a join; D.10 still stores the parsed
        # int for fast WHERE filtering.
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
            pg.ENUM(*_STATUS_VALUES, name="key_test_status", create_type=False),
            nullable=False,
            server_default=sa.text("'untested'::key_test_status"),
        ),
        sa.Column("test_error", sa.Text(), nullable=True),
        sa.Column("last_test_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_api_keys_owner_provider", "api_keys",
        ["owner_user_id", "provider"],
    )
    op.create_index("ix_api_keys_provider", "api_keys", ["provider"])
    op.create_index(
        "ix_api_keys_transit_version_open", "api_keys",
        ["transit_key_version"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_transit_version_open", table_name="api_keys")
    op.drop_index("ix_api_keys_provider", table_name="api_keys")
    op.drop_index("ix_api_keys_owner_provider", table_name="api_keys")
    op.drop_table("api_keys")
    op.execute("DROP TYPE key_test_status")
    op.execute("DROP TYPE api_key_provider")
