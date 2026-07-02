"""Observer agents: creator identity, disclosure flag, binding role, observations.

Adds ``chatrooms.created_by_user_id`` (nullable, backfilled from the
``chatroom.created`` audit trail — earliest actor wins; unmatched legacy rooms
stay NULL and fall back to moderator semantics, R28.02) and
``chatrooms.disclose_observers`` (default true, R28.09). Adds the
``chatroom_agent_role`` enum and ``chatroom_agents.role`` (default ``normal``,
R28.01). Creates ``agent_observations`` — observer output is stored here and
never in ``messages``, which is what makes every existing read path
structurally unable to leak it (R28.03).

Revision ID: 0041_observer_agents
Revises: 0040_message_attachment_extracted_text
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0041_observer_agents"
down_revision: str = "0040_message_attachment_extracted_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_agent_role = pg.ENUM("normal", "observer", name="chatroom_agent_role", create_type=False)

_BACKFILL_CREATED_BY = """
UPDATE chatrooms c
SET created_by_user_id = a.actor_user_id
FROM (
  SELECT DISTINCT ON (resource_id) resource_id, actor_user_id
  FROM audit_logs
  WHERE action = 'chatroom.created' AND resource_type = 'chatroom'
        AND actor_user_id IS NOT NULL
  ORDER BY resource_id, created_at ASC
) a
WHERE c.id = a.resource_id AND c.created_by_user_id IS NULL
"""


def upgrade() -> None:
    op.add_column(
        "chatrooms",
        sa.Column(
            "created_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "chatrooms",
        sa.Column("disclose_observers", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute(_BACKFILL_CREATED_BY)

    _agent_role.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "chatroom_agents",
        sa.Column(
            "role",
            _agent_role,
            nullable=False,
            server_default=sa.text("'normal'::chatroom_agent_role"),
        ),
    )

    op.create_table(
        "agent_observations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("metadata", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("trigger_message_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("release_target", pg.JSONB(), nullable=True),
        sa.Column(
            "released_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_observations_room",
        "agent_observations",
        ["chatroom_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_observations_room", table_name="agent_observations")
    op.drop_table("agent_observations")
    op.drop_column("chatroom_agents", "role")
    _agent_role.drop(op.get_bind(), checkfirst=True)
    op.drop_column("chatrooms", "disclose_observers")
    op.drop_column("chatrooms", "created_by_user_id")
