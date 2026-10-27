"""Class start and activity time limits ([R15.27], [R15.28], [R30.46]).

Every column is nullable and nothing pre-0106 reads them, so old code runs on
this schema unchanged. The two partial indexes serve the 30-second sweep, which
looks only for rows still waiting to be claimed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0106_class_start_and_time_limits"
down_revision: str = "0105_agent_bubble_colors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPE_LIMIT_CHECK = "ck_activity_types_default_time_limit"
_ROOM_LIMIT_CHECK = "ck_chatroom_activity_time_limits_minutes"
_ACTIVATION_LIMIT_CHECK = "ck_activity_activations_time_limit"
_DEADLINE_INDEX = "ix_activity_activations_unclaimed_deadline"
_CLASS_START_INDEX = "ix_chatrooms_pending_class_start"


def upgrade() -> None:
    op.add_column("activity_types", sa.Column("default_time_limit_minutes", sa.Integer, nullable=True))
    op.create_check_constraint(
        _TYPE_LIMIT_CHECK,
        "activity_types",
        "default_time_limit_minutes IS NULL OR default_time_limit_minutes BETWEEN 1 AND 180",
    )

    op.create_table(
        "chatroom_activity_time_limits",
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "activity_type_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # NULL is an explicit "no limit" for this room, distinct from having no row
        # (which falls back to the type default).
        sa.Column("time_limit_minutes", sa.Integer, nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "time_limit_minutes IS NULL OR time_limit_minutes BETWEEN 1 AND 180",
            name=_ROOM_LIMIT_CHECK,
        ),
    )

    op.add_column("activity_activations", sa.Column("time_limit_minutes", sa.Integer, nullable=True))
    op.add_column(
        "activity_activations", sa.Column("deadline_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "activity_activations",
        sa.Column("deadline_notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "activity_activations",
        sa.Column("completion_notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        _ACTIVATION_LIMIT_CHECK,
        "activity_activations",
        "(time_limit_minutes IS NULL) = (deadline_at IS NULL)",
    )
    op.create_index(
        _DEADLINE_INDEX,
        "activity_activations",
        ["deadline_at"],
        postgresql_where=sa.text(
            "status = 'active' AND deadline_at IS NOT NULL AND deadline_notified_at IS NULL"
        ),
    )

    op.add_column("chatrooms", sa.Column("class_start_config", pg.JSONB, nullable=True))
    op.add_column(
        "chatrooms", sa.Column("class_first_arrival_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("chatrooms", sa.Column("class_started_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.create_index(
        _CLASS_START_INDEX,
        "chatrooms",
        ["id"],
        postgresql_where=sa.text(
            "class_start_config IS NOT NULL AND class_started_at IS NULL AND deleted_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(_CLASS_START_INDEX, table_name="chatrooms")
    op.drop_column("chatrooms", "class_started_at")
    op.drop_column("chatrooms", "class_first_arrival_at")
    op.drop_column("chatrooms", "class_start_config")

    op.drop_index(_DEADLINE_INDEX, table_name="activity_activations")
    op.drop_constraint(_ACTIVATION_LIMIT_CHECK, "activity_activations", type_="check")
    op.drop_column("activity_activations", "completion_notified_at")
    op.drop_column("activity_activations", "deadline_notified_at")
    op.drop_column("activity_activations", "deadline_at")
    op.drop_column("activity_activations", "time_limit_minutes")

    op.drop_table("chatroom_activity_time_limits")

    op.drop_constraint(_TYPE_LIMIT_CHECK, "activity_types", type_="check")
    op.drop_column("activity_types", "default_time_limit_minutes")
