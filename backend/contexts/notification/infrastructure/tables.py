"""SQLAlchemy Core tables for the notification context."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

notifications = sa.Table(
    "notifications",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("user_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=True),
    sa.Column("metadata", pg.JSONB, nullable=False,
              server_default=sa.text("'{}'::jsonb")),
    sa.Column("read_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)

__all__ = ["notifications"]
