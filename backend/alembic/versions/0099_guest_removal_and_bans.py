"""Remove and ban a single anonymous guest ([R13.07a]).

``guest_sessions.revoked_at`` marks a session a moderator removed. The row is
kept, not deleted, because its display name still labels the guest's past
messages ([R13.33]); the room access layer refuses a revoked session.

``chatroom_guest_bans`` outlives guest-session retention (idle sessions are
purged after 30 days), so ``guest_session_id`` carries no foreign key. The ban
keys on the session and on a sha256 of the browser id the session joined with,
which is what a resume matches on; the id itself is not stored.

Dossier: ``docs/tasks/2026-10-05-guest-kick-and-ban``.

Revision ID: 0099_guest_removal_and_bans
Revises: 0098_guest_identity_columns
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0099_guest_removal_and_bans"
down_revision: str = "0098_guest_identity_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("guest_sessions", sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True))

    op.create_table(
        "chatroom_guest_bans",
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
        sa.Column("guest_session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("browser_id_hash", sa.Text, nullable=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column(
            "created_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("chatroom_id", "guest_session_id", name="uq_chatroom_guest_bans_room_session"),
    )
    op.create_index(
        "ix_chatroom_guest_bans_room_browser",
        "chatroom_guest_bans",
        ["chatroom_id", "browser_id_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_chatroom_guest_bans_room_browser", table_name="chatroom_guest_bans")
    op.drop_table("chatroom_guest_bans")
    op.drop_column("guest_sessions", "revoked_at")
