"""Index distinct human authors per chatroom.

Revision ID: 0034_messages_sender_index
Revises: 0033_user_display_name

Backs ``MessageRepository.distinct_user_sender_ids`` (the chat member roster).
Without this, resolving author display names ran ``SELECT DISTINCT sender_id``
as a heap scan over a room's whole message history on every roster fetch. The
partial index over ``(chatroom_id, sender_id)`` on live user messages lets that
query (ordered + capped) read sorted index entries and stop early, so the cost
tracks the number of distinct authors rather than the message volume.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034_messages_sender_index"
down_revision: str | Sequence[str] | None = "0033_user_display_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_messages_chatroom_sender "
        "ON messages (chatroom_id, sender_id) "
        "WHERE sender_type = 'user' AND sender_id IS NOT NULL AND deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_chatroom_sender")
