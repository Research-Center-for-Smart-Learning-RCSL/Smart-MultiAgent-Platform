"""Anonymous guest sessions for chatroom access (R5.04, R13.06).

Revision ID: 0085_guest_sessions
Revises: 0084_email_domain_policies

Adds ``"guest"`` to the ``message_sender_type`` PG ENUM and creates the
``guest_sessions`` table. Guest sessions are standalone rows with no FK to
``users`` -- an anonymous visitor holds a chatroom-scoped JWT backed by this
table, without a user account.

``ALTER TYPE ... ADD VALUE`` cannot run in the same transaction as a query
that uses the new value (PostgreSQL restriction). This migration never
queries the new value, so it is safe standalone, but a later migration that
inserts a ``"guest"`` sender_type row must not share a transaction with this
one (see 0083 for the same caveat).

The ``refresh_token_hash`` stores an Argon2 hash of the opaque refresh
token, following the same pattern as ``sessions.refresh_token_hash``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0085_guest_sessions"
down_revision: str = "0084_email_domain_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE message_sender_type ADD VALUE IF NOT EXISTS 'guest'")

    op.create_table(
        "guest_sessions",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("browser_id", sa.Text, nullable=True),
        sa.Column("refresh_token_hash", sa.Text, nullable=False, unique=True),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_guest_sessions_chatroom_browser",
        "guest_sessions",
        ["chatroom_id", "browser_id"],
        unique=False,
        postgresql_where=sa.text("browser_id IS NOT NULL"),
    )

    op.create_index(
        "ix_guest_sessions_chatroom_last_seen",
        "guest_sessions",
        ["chatroom_id", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_guest_sessions_chatroom_last_seen", table_name="guest_sessions")
    op.drop_index("ix_guest_sessions_chatroom_browser", table_name="guest_sessions")
    op.drop_table("guest_sessions")
    # "guest" remains in message_sender_type -- PostgreSQL cannot drop an enum
    # value in place, and no existing row holds it, so it sits harmlessly.
