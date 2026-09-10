"""Add encrypted_proxy_headers column to api_keys.

Moves proxy_headers from plaintext config JSONB into a dedicated encrypted
column. Existing rows with proxy_headers in config are migrated: the
plaintext dict is copied into the new column (encryption happens at
application level on next access), and the key is removed from config.

The down path copies encrypted_proxy_headers back to config JSONB for
rollback (acceptable: the column is dropped so no duplicate remains).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0088_encrypt_proxy_headers"
down_revision: str | Sequence[str] | None = "0087_prompt_assistant_persona_presets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("encrypted_proxy_headers", pg.JSONB, nullable=True))

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE api_keys
            SET encrypted_proxy_headers = config -> 'proxy_headers',
                config = config - 'proxy_headers'
            WHERE config ? 'proxy_headers'
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE api_keys
            SET config = config || jsonb_build_object('proxy_headers', encrypted_proxy_headers)
            WHERE encrypted_proxy_headers IS NOT NULL
            """
        )
    )
    op.drop_column("api_keys", "encrypted_proxy_headers")
