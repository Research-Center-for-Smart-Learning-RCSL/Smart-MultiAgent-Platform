"""GraphRAG owner decoupling — expand step (Phase 1, R11.05/R11.07/R11.08).

Introduces the discriminated owner model for ``graphrag_configs`` without
removing the legacy ``agent_id`` yet (expand -> migrate -> contract; the
contract step is ``0044_graphrag_drop_agent_id``). Creates the
``agent_groups`` / ``agent_group_members`` substrate, adds the three typed
owner FK columns + ``owner_kind`` discriminator (all nullable this step), and
backfills one singleton ``agent_group`` per config so the former per-agent
scope is reproduced exactly. Every config — including those whose agent is
soft-deleted (Q-3) — gets an owner; a config left with a NULL owner would
break the CHECK the contract step adds.

Reversible: the downgrade drops the substrate and the owner columns; the
still-present ``agent_id`` is the source of truth throughout the expand phase.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0043_graphrag_owner"
down_revision: str | Sequence[str] | None = "0042_prompt_studio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER_KINDS: tuple[str, ...] = ("chatroom", "agent_group", "workspace")


def upgrade() -> None:
    op.execute("CREATE TYPE owner_kind AS ENUM (" + ", ".join(f"'{v}'" for v in _OWNER_KINDS) + ")")

    op.create_table(
        "agent_groups",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # One live group per name within a project (soft-deleted rows may collide).
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_groups_project_name_active "
        "ON agent_groups (project_id, name) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "agent_group_members",
        sa.Column(
            "agent_group_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agent_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_group_id", "agent_id"),
    )

    # Owner columns on graphrag_configs (all nullable this step; owner_kind and
    # the CHECK/NOT NULL land in the contract migration once every row is filled).
    op.add_column(
        "graphrag_configs",
        sa.Column(
            "owner_kind",
            pg.ENUM(*_OWNER_KINDS, name="owner_kind", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "graphrag_configs",
        sa.Column(
            "owner_chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "graphrag_configs",
        sa.Column(
            "owner_agent_group_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("agent_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "graphrag_configs",
        sa.Column(
            "owner_workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    # At most one config per owner of each kind.
    for kind in _OWNER_KINDS:
        col = f"owner_{kind}_id"
        op.execute(
            f"CREATE UNIQUE INDEX uq_graphrag_configs_owner_{kind} "
            f"ON graphrag_configs ({col}) WHERE {col} IS NOT NULL"
        )

    _backfill_singleton_groups()


def _backfill_singleton_groups() -> None:
    """One singleton agent_group per config, member = former agent_id (Q-2/Q-3).

    Names are ``graphrag-owner-{agent_id}`` — ``agent_id`` is globally unique and
    stable, so the synthetic name never collides on the partial unique index the
    way a soft-deleted/live ``agents.name`` pair could.
    """
    conn = op.get_bind()

    configs_t = sa.table(
        "graphrag_configs",
        sa.column("id", pg.UUID(as_uuid=True)),
        sa.column("project_id", pg.UUID(as_uuid=True)),
        sa.column("agent_id", pg.UUID(as_uuid=True)),
        sa.column("owner_agent_group_id", pg.UUID(as_uuid=True)),
        sa.column("owner_kind", pg.ENUM(*_OWNER_KINDS, name="owner_kind", create_type=False)),
    )
    groups_t = sa.table(
        "agent_groups",
        sa.column("id", pg.UUID(as_uuid=True)),
        sa.column("project_id", pg.UUID(as_uuid=True)),
        sa.column("name", sa.Text),
    )
    members_t = sa.table(
        "agent_group_members",
        sa.column("agent_group_id", pg.UUID(as_uuid=True)),
        sa.column("agent_id", pg.UUID(as_uuid=True)),
    )

    rows = conn.execute(sa.select(configs_t.c.id, configs_t.c.project_id, configs_t.c.agent_id)).fetchall()

    for config_id, project_id, agent_id in rows:
        group_id = uuid.uuid4()
        conn.execute(
            groups_t.insert().values(
                id=group_id,
                project_id=project_id,
                name=f"graphrag-owner-{agent_id}",
            )
        )
        conn.execute(members_t.insert().values(agent_group_id=group_id, agent_id=agent_id))
        conn.execute(
            configs_t.update()
            .where(configs_t.c.id == config_id)
            .values(owner_agent_group_id=group_id, owner_kind="agent_group")
        )


def downgrade() -> None:
    for kind in _OWNER_KINDS:
        op.execute(f"DROP INDEX IF EXISTS uq_graphrag_configs_owner_{kind}")
    op.drop_column("graphrag_configs", "owner_workspace_id")
    op.drop_column("graphrag_configs", "owner_agent_group_id")
    op.drop_column("graphrag_configs", "owner_chatroom_id")
    op.drop_column("graphrag_configs", "owner_kind")

    op.drop_table("agent_group_members")
    op.execute("DROP INDEX IF EXISTS uq_agent_groups_project_name_active")
    op.drop_table("agent_groups")

    op.execute("DROP TYPE owner_kind")
