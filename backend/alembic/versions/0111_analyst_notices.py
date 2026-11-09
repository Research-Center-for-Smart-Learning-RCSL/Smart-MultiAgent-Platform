"""Analyst notices to a room's teacher agent.

Spec: docs/tasks/2026-11-09-classroom-agent-collaboration/spec.md, milestone 2
([R34.04], [R34.05]).

A new table nothing before 0111 reads, so old code runs on this schema unchanged.
Status is not stored: a notice with a decision is answered, one without is
pending until 15 minutes old and unanswered after, so no sweeper has to keep a
column true.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0111_analyst_notices"
down_revision: str = "0110_classroom_roles_and_alerts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "analyst_notices"
_ROOM_INDEX = "ix_analyst_notices_room_created"
_SUBJECT_INDEX = "ix_analyst_notices_subject"


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
            "analyst_agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analyst_binding_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "teacher_agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("teacher_binding_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("error_kind", sa.Text, nullable=False),
        sa.Column("error", sa.Text, nullable=False),
        sa.Column("suggestion", sa.Text, nullable=False),
        sa.Column(
            "about_message_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision", sa.Text, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("responded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("error_kind IN ('content', 'strategy')", name="ck_analyst_notices_error_kind"),
        sa.CheckConstraint(
            "char_length(error) BETWEEN 1 AND 1000 AND char_length(suggestion) BETWEEN 1 AND 1000",
            name="ck_analyst_notices_text_length",
        ),
        sa.CheckConstraint(
            "(decision IS NULL AND reason IS NULL AND responded_at IS NULL) "
            "OR (decision IN ('accepted', 'partly_accepted', 'declined') "
            "AND char_length(reason) BETWEEN 1 AND 500 AND responded_at IS NOT NULL)",
            name="ck_analyst_notices_response",
        ),
    )
    op.create_index(_ROOM_INDEX, _TABLE, ["chatroom_id", "created_at"])
    op.create_index(
        _SUBJECT_INDEX,
        _TABLE,
        ["about_message_id"],
        postgresql_where=sa.text("about_message_id IS NOT NULL AND decision IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(_SUBJECT_INDEX, table_name=_TABLE)
    op.drop_index(_ROOM_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
