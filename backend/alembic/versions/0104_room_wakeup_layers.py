"""Room-owned wake-up layers on each agent binding ([R15.09], [R15.25]).

Each ``chatroom_agents`` row gains an immutable ``binding_id`` (a fresh value
per bind, so an unbind/rebind is a new incarnation), a ``wakeup_version`` for
optimistic concurrency, a sparse human room layer, a sparse agent runtime
layer, and the time the runtime layer's refresh interval started. Empty layers
mean "inherit the designer's defaults", which is how every existing binding
starts.

``agents.wakeup_config`` becomes the designer's defaults alone. Until now the
agent's own ``update_wakeup`` calls rewrote it in place and the periodic refresh
restored ``wakeup_authored_snapshot``, so a stored config could hold runtime
drift no room can be assigned. Where a snapshot exists it is the designer's
last authored value and is restored here; an explicit ``{}`` snapshot is a real
value and is restored too. Where none exists, as SQL ``NULL`` or as the JSON
``null`` that agent creates have written, the stored config is kept as the
baseline. Every value this overwrites is first copied to
``agent_wakeup_config_backup_0104``, a table rather than a log line, for a
rollback that needs them. The update is idempotent.

The downgrade restores the backed-up configs and drops the new columns and
the backup. That discards every room setting made since, which is why the
runbook's rollback does not run it: code from before this revision ignores
the new columns, so the schema can stay.

Dossier: ``docs/tasks/2026-10-16-crat-facilitation-phase2-classroom-context-and-cadence``.

Revision ID: 0104_room_wakeup_layers
Revises: 0103_agent_group_member_read_filter
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0104_room_wakeup_layers"
down_revision: str = "0103_agent_group_member_read_filter"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKUP = "agent_wakeup_config_backup_0104"


def upgrade() -> None:
    op.add_column(
        "chatroom_agents",
        sa.Column(
            "binding_id", pg.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
    )
    op.create_unique_constraint("uq_chatroom_agents_binding_id", "chatroom_agents", ["binding_id"])
    op.add_column(
        "chatroom_agents",
        sa.Column("wakeup_version", sa.Integer, nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "chatroom_agents",
        sa.Column("wakeup_room_overrides", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "chatroom_agents",
        sa.Column(
            "wakeup_runtime_overrides", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.add_column(
        "chatroom_agents",
        sa.Column("wakeup_runtime_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The refresh sweep reads only bindings holding a runtime layer.
    op.create_index(
        "ix_chatroom_agents_runtime_started_at",
        "chatroom_agents",
        ["wakeup_runtime_started_at"],
        postgresql_where=sa.text("wakeup_runtime_started_at IS NOT NULL"),
    )

    op.create_table(
        _BACKUP,
        sa.Column("agent_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("wakeup_config", pg.JSONB, nullable=False),
        sa.Column(
            "backed_up_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    for statement in data_step_sql():
        op.execute(sa.text(statement))


def data_step_sql() -> tuple[str, ...]:
    """Back up, then restore, every config that differs from its authored snapshot.

    A function so the db-tier test runs exactly these statements. Only a JSON
    object counts as a snapshot: the column is written with a JSON ``null`` (not
    SQL ``NULL``) for agents created without one, and restoring that would blank
    their config. ``jsonb_typeof`` is NULL for SQL ``NULL`` too. The version bump
    makes an editor holding the drifted config get a 409 rather than save it back
    as the designer's value.
    """
    return (
        "INSERT INTO agent_wakeup_config_backup_0104 (agent_id, wakeup_config) "
        "SELECT id, wakeup_config FROM agents "
        "WHERE jsonb_typeof(wakeup_authored_snapshot) = 'object' "
        "AND wakeup_config IS DISTINCT FROM wakeup_authored_snapshot "
        "ON CONFLICT (agent_id) DO NOTHING",
        "UPDATE agents SET wakeup_config = wakeup_authored_snapshot, version = version + 1 "
        "WHERE jsonb_typeof(wakeup_authored_snapshot) = 'object' "
        "AND wakeup_config IS DISTINCT FROM wakeup_authored_snapshot",
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE agents SET wakeup_config = b.wakeup_config, version = agents.version + 1 "
            "FROM agent_wakeup_config_backup_0104 b WHERE agents.id = b.agent_id"
        )
    )
    op.drop_table(_BACKUP)
    op.drop_index("ix_chatroom_agents_runtime_started_at", table_name="chatroom_agents")
    op.drop_column("chatroom_agents", "wakeup_runtime_started_at")
    op.drop_column("chatroom_agents", "wakeup_runtime_overrides")
    op.drop_column("chatroom_agents", "wakeup_room_overrides")
    op.drop_column("chatroom_agents", "wakeup_version")
    op.drop_constraint("uq_chatroom_agents_binding_id", "chatroom_agents", type_="unique")
    op.drop_column("chatroom_agents", "binding_id")
