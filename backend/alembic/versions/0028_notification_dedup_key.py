"""Add notifications.dedup_key + partial unique index for atomic dedup.

Revision ID: 0028_notification_dedup_key
Revises: 0027_audit_retention_grant
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_notification_dedup_key"
down_revision: str | Sequence[str] | None = "0027_audit_retention_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOTIF-DEDUP: replace the racy SELECT-then-INSERT dedup check in
    # NotificationService.send with a DB constraint. Two concurrent identical
    # sends now collide on this partial unique index; `INSERT ... ON CONFLICT
    # DO NOTHING` lets exactly one win. The index is partial so rows without a
    # dedup_key (none currently, but kept for forward compatibility) are not
    # constrained.
    op.add_column(
        "notifications",
        sa.Column("dedup_key", sa.Text(), nullable=True),
    )
    op.create_index(
        "uq_notifications_user_id_dedup_key",
        "notifications",
        ["user_id", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notifications_user_id_dedup_key", table_name="notifications")
    op.drop_column("notifications", "dedup_key")
