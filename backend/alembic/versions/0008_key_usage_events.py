"""key_usage_events + key_usage_daily — D.9 / §21.1 keys usage.

Monthly partitioning: the live writer always targets the "current month"
partition so hot inserts land on a narrow index. The partition creation
itself is done by the retention worker (I.4); this migration creates the
parent table, the DEFAULT partition (so inserts never fail if the worker
missed a month), and the aggregate table.

SoC: monthly partitions are an operational concern. This migration is happy
to ship with just the default partition; retention code creates monthly
partitions and detaches partitions > 13 months.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0008_key_usage_events"
down_revision: str | Sequence[str] | None = "0007_key_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE key_usage_events (
          id bigserial NOT NULL,
          key_id uuid NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
          agent_id uuid NULL,
          parent_agent_id uuid NULL,
          chatroom_id uuid NULL,
          input_tokens int NOT NULL DEFAULT 0,
          output_tokens int NOT NULL DEFAULT 0,
          request_ms int NOT NULL DEFAULT 0,
          http_status int NULL,
          error_code text NULL,
          at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (id, at)
        ) PARTITION BY RANGE (at)
        """
    )
    op.execute(
        """
        CREATE TABLE key_usage_events_default
          PARTITION OF key_usage_events DEFAULT
        """
    )
    op.create_index(
        "ix_key_usage_events_key_at", "key_usage_events",
        ["key_id", sa.text("at DESC")],
    )
    op.create_index(
        "ix_key_usage_events_agent_at", "key_usage_events",
        ["agent_id", sa.text("at DESC")],
    )

    op.create_table(
        "key_usage_daily",
        sa.Column("key_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("errors", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("key_id", "day", name="pk_key_usage_daily"),
    )
    op.create_index("ix_key_usage_daily_day", "key_usage_daily", [sa.text("day DESC")])


def downgrade() -> None:
    op.drop_index("ix_key_usage_daily_day", table_name="key_usage_daily")
    op.drop_table("key_usage_daily")
    op.drop_index("ix_key_usage_events_agent_at", table_name="key_usage_events")
    op.drop_index("ix_key_usage_events_key_at", table_name="key_usage_events")
    op.execute("DROP TABLE IF EXISTS key_usage_events_default")
    op.execute("DROP TABLE IF EXISTS key_usage_events")
