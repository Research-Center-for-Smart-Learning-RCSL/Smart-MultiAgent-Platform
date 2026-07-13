"""room-level activity activations.

An activation is the facilitator-controlled room window in which submissions
for one activity type are accepted. The partial-unique index is the durable
one-active-per-room invariant.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0050_activity_activations"
down_revision: str | Sequence[str] | None = "0049_activities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    activation_status = pg.ENUM("active", "ended", name="activation_status")
    bind = op.get_bind()
    activation_status.create(bind, checkfirst=True)

    op.create_table(
        "activity_activations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activity_type_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "started_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            pg.ENUM("active", "ended", name="activation_status", create_type=False),
            nullable=False,
            server_default=sa.text("'active'::activation_status"),
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_activity_activations_chatroom", "activity_activations", ["chatroom_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_activity_activations_active "
        "ON activity_activations (chatroom_id) WHERE status = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_activity_activations_active")
    op.drop_index("ix_activity_activations_chatroom", table_name="activity_activations")
    op.drop_table("activity_activations")
    pg.ENUM(name="activation_status").drop(op.get_bind(), checkfirst=True)
