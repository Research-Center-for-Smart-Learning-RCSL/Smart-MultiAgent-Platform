"""Add access_jti to admin_impersonation_sessions for JWT revocation on end.

Revision ID: 0026_impersonation_access_jti
Revises: 0025_notifications
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0026_impersonation_access_jti"
down_revision: str | Sequence[str] | None = "0025_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_impersonation_sessions",
        sa.Column("access_jti", pg.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("admin_impersonation_sessions", "access_jti")
