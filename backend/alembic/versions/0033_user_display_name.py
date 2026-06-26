"""Add display_name to users.

Revision ID: 0033_user_display_name
Revises: 0032_audit_retention_delete_grant

Nullable VARCHAR(50) so a user can set an optional, non-unique display name
shown in the UI and as the chat-message author label. Email remains the login
identifier; this column never participates in authentication or uniqueness.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033_user_display_name"
down_revision: str | Sequence[str] | None = "0032_audit_retention_delete_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "display_name")
