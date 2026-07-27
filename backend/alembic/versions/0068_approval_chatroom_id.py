"""Persist the room an approval gate was raised in (F-13).

``chatroom_id`` was threaded through every approval call (create_gate,
announce_gate, cast_vote, handle_timeout) as a transient publish parameter
only -- never stored -- so there was no way to discover an approval gate
whose ``approval.requested`` WS frame was missed while a client was
disconnected. Nullable and unindexed by anything but this FK: old code paths
that never pass it keep working, and pre-migration rows are simply absent
from the new chatroom-scoped list endpoint (deliberately not backfilled, see
the task dossier -- the room is only partly recoverable after the fact, and
a partial backfill would be silently wrong for the unrecoverable rows).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0068_approval_chatroom_id"
down_revision: str | Sequence[str] | None = "0067_wakeup_last_refreshed_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("approvals", "chatroom_id")
