"""Classroom roles on room bindings, and alert reaction times on observations.

Spec: docs/tasks/2026-11-09-classroom-agent-collaboration/spec.md, milestone 1
([R34.01], [R34.02]).

Every new column has a default or is nullable and nothing before 0110 reads
them, so old code runs on this schema unchanged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0110_classroom_roles_and_alerts"
down_revision: str = "0109_creativity_judging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_ENUM = pg.ENUM("none", "teacher", "peer", "analyst", name="classroom_agent_role")
_ROLE_CHECK = "ck_chatroom_agents_classroom_role_binding"
_TEACHER_UNIQUE = "uq_chatroom_agents_one_teacher"
_VIA_CHECK = "ck_agent_observations_first_viewed_via"
_ACTION_CHECK = "ck_agent_observations_first_action_kind"
_ALERT_CHECK = "ck_agent_observations_alert_reason"
_ALERT_INDEX = "ix_agent_observations_alerts"


def upgrade() -> None:
    _ROLE_ENUM.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "chatroom_agents",
        sa.Column(
            "classroom_role",
            pg.ENUM(name="classroom_agent_role", create_type=False),
            nullable=False,
            server_default=sa.text("'none'::classroom_agent_role"),
        ),
    )
    op.create_check_constraint(
        _ROLE_CHECK,
        "chatroom_agents",
        "(classroom_role IN ('teacher', 'peer') AND role = 'normal') "
        "OR (classroom_role = 'analyst' AND role = 'observer') "
        "OR classroom_role = 'none'",
    )
    op.create_index(
        _TEACHER_UNIQUE,
        "chatroom_agents",
        ["chatroom_id"],
        unique=True,
        postgresql_where=sa.text("classroom_role = 'teacher'"),
    )

    op.add_column(
        "agent_observations",
        sa.Column("is_alert", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.add_column("agent_observations", sa.Column("alert_reason", sa.Text, nullable=True))
    op.add_column(
        "agent_observations", sa.Column("first_viewed_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("agent_observations", sa.Column("first_viewed_via", sa.Text, nullable=True))
    op.add_column(
        "agent_observations", sa.Column("first_action_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("agent_observations", sa.Column("first_action_kind", sa.Text, nullable=True))
    op.create_check_constraint(
        _ALERT_CHECK, "agent_observations", "alert_reason IS NULL OR char_length(alert_reason) <= 200"
    )
    op.create_check_constraint(
        _VIA_CHECK,
        "agent_observations",
        "(first_viewed_at IS NULL AND first_viewed_via IS NULL) "
        "OR (first_viewed_at IS NOT NULL AND first_viewed_via IN ('panel', 'dashboard'))",
    )
    op.create_check_constraint(
        _ACTION_CHECK,
        "agent_observations",
        "(first_action_at IS NULL AND first_action_kind IS NULL) "
        "OR (first_action_at IS NOT NULL AND first_action_kind IN "
        "('released', 'deleted', 'room_message', 'activity_started', 'activity_ended'))",
    )
    op.create_index(
        _ALERT_INDEX,
        "agent_observations",
        ["chatroom_id", "created_at"],
        postgresql_where=sa.text("is_alert"),
    )


def downgrade() -> None:
    op.drop_index(_ALERT_INDEX, table_name="agent_observations")
    op.drop_constraint(_ACTION_CHECK, "agent_observations", type_="check")
    op.drop_constraint(_VIA_CHECK, "agent_observations", type_="check")
    op.drop_constraint(_ALERT_CHECK, "agent_observations", type_="check")
    for col in (
        "first_action_kind",
        "first_action_at",
        "first_viewed_via",
        "first_viewed_at",
        "alert_reason",
        "is_alert",
    ):
        op.drop_column("agent_observations", col)
    op.drop_index(_TEACHER_UNIQUE, table_name="chatroom_agents")
    op.drop_constraint(_ROLE_CHECK, "chatroom_agents", type_="check")
    op.drop_column("chatroom_agents", "classroom_role")
    _ROLE_ENUM.drop(op.get_bind(), checkfirst=False)
