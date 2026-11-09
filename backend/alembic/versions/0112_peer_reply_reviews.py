"""Teacher-agent review of peer-agent replies.

Spec: docs/tasks/2026-11-09-classroom-agent-collaboration/spec.md, milestone 3
([R34.06]).

A new table nothing before 0112 reads, so old code runs on this schema unchanged.
A review leaves ``pending`` exactly once, by a conditional update, and the
posted message id is written in the same statement; ``due_at`` lets a review
whose job was killed read as expired without a sweeper.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0112_peer_reply_reviews"
down_revision: str = "0111_analyst_notices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "peer_reply_reviews"
_ROOM_INDEX = "ix_peer_reply_reviews_room_created"
_UNTOLD_INDEX = "ix_peer_reply_reviews_untold_rejections"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "peer_agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("peer_binding_id", pg.UUID(as_uuid=True), nullable=False),
        # Null when the room had no teacher binding: the review is born expired (Q-9).
        sa.Column(
            "teacher_agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("teacher_binding_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("draft", sa.Text, nullable=False),
        sa.Column("reply_metadata", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "trigger_message_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("revised_text", sa.Text, nullable=True),
        sa.Column("expire_reason", sa.Text, nullable=True),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "posted_message_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("peer_told_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'modified', 'rejected', 'expired')",
            name="ck_peer_reply_reviews_status",
        ),
        sa.CheckConstraint("attempt BETWEEN 1 AND 3", name="ck_peer_reply_reviews_attempt"),
        sa.CheckConstraint("char_length(draft) >= 1", name="ck_peer_reply_reviews_draft"),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL AND reason IS NULL AND expire_reason IS NULL) "
            "OR (status IN ('approved', 'modified', 'rejected') AND decided_at IS NOT NULL "
            "AND char_length(reason) BETWEEN 1 AND 500) "
            "OR (status = 'expired' AND decided_at IS NOT NULL AND expire_reason IS NOT NULL)",
            name="ck_peer_reply_reviews_decision",
        ),
        sa.CheckConstraint(
            "(status = 'modified') = (revised_text IS NOT NULL)",
            name="ck_peer_reply_reviews_revised",
        ),
        sa.CheckConstraint(
            "status = 'pending' OR status = 'expired' OR teacher_agent_id IS NOT NULL",
            name="ck_peer_reply_reviews_teacher",
        ),
    )
    op.create_index(_ROOM_INDEX, _TABLE, ["chatroom_id", "created_at"])
    op.create_index(
        _UNTOLD_INDEX,
        _TABLE,
        ["chatroom_id", "peer_agent_id"],
        postgresql_where=sa.text("status = 'rejected' AND peer_told_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(_UNTOLD_INDEX, table_name=_TABLE)
    op.drop_index(_ROOM_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
