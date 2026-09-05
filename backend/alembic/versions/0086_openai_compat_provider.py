"""Add openai_compat provider and per-key config column (R7.01, R7.16).

Revision ID: 0086_openai_compat_provider
Revises: 0085_guest_sessions

Widens two PG ENUMs (``api_key_provider``, ``agent_model_hint``) to include
``'openai_compat'`` and adds a ``config`` JSONB column to ``api_keys`` with
a default of ``'{}'::jsonb``.

``ALTER TYPE ... ADD VALUE`` is non-transactional in PostgreSQL and cannot
run inside an explicit transaction block. Alembic's ``autocommit_block()``
handles this correctly.

The ``config`` column defaults to ``'{}'`` so every existing key gains an
empty config object without a data fixup. The column is forward-compatible:
old code that does not read it is unaffected.

ENUM values cannot be removed in PostgreSQL without recreating the type, so
the downgrade drops only the ``config`` column and leaves the enum values in
place (standard practice in this project since 0005).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg


revision = "0086_openai_compat_provider"
down_revision = "0085_guest_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE api_key_provider ADD VALUE IF NOT EXISTS 'openai_compat'")
        op.execute("ALTER TYPE agent_model_hint ADD VALUE IF NOT EXISTS 'openai_compat'")

    op.add_column(
        "api_keys",
        sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "config")
