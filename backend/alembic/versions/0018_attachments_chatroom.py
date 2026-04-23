"""message_attachments → add chatroom_id + uploader — F.5.

Attachments are created BEFORE the message (tus / single-shot upload both
return an attachment_id that the client then sends in `MessageSendIn`), so
we need `chatroom_id` on the row itself: without it we can't ACL-check
fetches or bind-time ownership against the caller's room before the message
exists. `uploaded_by_user_id` is recorded for audit trail symmetry.

`message_id` is kept nullable (§21.1) because:
  - during the upload→send window (pre-bind) it is genuinely NULL,
  - after retention-purge of a parent message we keep the attachment row
    long enough for the bucket lifecycle to expire its object (3 days),
    and `message_attachments.expires_at` drives the sweep independently.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0018_attachments_chatroom"
down_revision: str | Sequence[str] | None = "0017_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "message_attachments",
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "message_attachments",
        sa.Column(
            "uploaded_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Partial index for the pre-bind lookup (list attachments the caller
    # uploaded into a room that have not yet been bound to a message).
    op.execute(
        "CREATE INDEX ix_message_attachments_unbound "
        "ON message_attachments (chatroom_id, uploaded_by_user_id) "
        "WHERE message_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_attachments_unbound")
    op.drop_column("message_attachments", "uploaded_by_user_id")
    op.drop_column("message_attachments", "chatroom_id")
