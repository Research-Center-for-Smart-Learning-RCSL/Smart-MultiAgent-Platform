"""GraphRAG owner decoupling — contract step (Phase 1 M3, R11.05/R11.07/R11.08).

Removes the legacy 1:1 anchors now that ownership flows through the discriminated
owner model (0043 expand + the M2 membership resolver):

- ``owner_kind`` becomes NOT NULL and gains the exactly-one-owner CHECK (every
  live row was filled by the 0043 backfill).
- ``graphrag_configs.agent_id`` (+ its UNIQUE and FK) is dropped — ownership is
  ``owner_agent_group_id``; the owning agent is derived from the singleton
  group's membership.
- The reverse pointer ``agents.graphrag_config_id`` (+ ``fk_agents_graphrag_config``)
  is dropped — retrieval resolves configs through the membership join, so the
  single-valued back-reference is dead.

Reversible against freshly-migrated singleton data: the downgrade reconstructs
``agent_id`` from each config's singleton group member and rewires the reverse
pointer from ownership, then restores the expand-phase nullability.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0044_graphrag_drop_agent_id"
down_revision: str | Sequence[str] | None = "0043_graphrag_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Exactly one owner FK non-null, matching owner_kind (0042 _SCOPE_CHECK style).
_OWNER_CHECK = (
    "(owner_kind = 'chatroom' AND owner_chatroom_id IS NOT NULL "
    "AND owner_agent_group_id IS NULL AND owner_workspace_id IS NULL) "
    "OR (owner_kind = 'agent_group' AND owner_agent_group_id IS NOT NULL "
    "AND owner_chatroom_id IS NULL AND owner_workspace_id IS NULL) "
    "OR (owner_kind = 'workspace' AND owner_workspace_id IS NOT NULL "
    "AND owner_chatroom_id IS NULL AND owner_agent_group_id IS NULL)"
)


def upgrade() -> None:
    # Every live config was assigned an agent_group owner by the 0043 backfill.
    op.alter_column("graphrag_configs", "owner_kind", nullable=False)
    op.create_check_constraint("ck_graphrag_configs_owner", "graphrag_configs", _OWNER_CHECK)

    # Drop the legacy 1:1 anchor. Postgres drops the dependent UNIQUE
    # (graphrag_configs_agent_id_key) and FK (graphrag_configs_agent_id_fkey)
    # with the column.
    op.drop_column("graphrag_configs", "agent_id")

    # Drop the single-valued reverse pointer (dead after the membership resolver).
    op.drop_constraint("fk_agents_graphrag_config", "agents", type_="foreignkey")
    op.drop_column("agents", "graphrag_config_id")


def downgrade() -> None:
    # 1. Restore the reverse pointer column + FK (SET NULL, as in 0013).
    op.add_column(
        "agents",
        sa.Column("graphrag_config_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agents_graphrag_config",
        "agents",
        "graphrag_configs",
        ["graphrag_config_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2. Restore agent_id as nullable, backfill from the singleton group member,
    #    then reinstate NOT NULL + UNIQUE + FK.
    op.add_column(
        "graphrag_configs",
        sa.Column("agent_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE graphrag_configs SET agent_id = ("
        "SELECT m.agent_id FROM agent_group_members m "
        "WHERE m.agent_group_id = graphrag_configs.owner_agent_group_id LIMIT 1)"
    )
    op.alter_column("graphrag_configs", "agent_id", nullable=False)
    op.create_unique_constraint("graphrag_configs_agent_id_key", "graphrag_configs", ["agent_id"])
    op.create_foreign_key(
        "graphrag_configs_agent_id_fkey",
        "graphrag_configs",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 3. Rewire the reverse pointer from ownership (best-effort — the original
    #    was only set for explicitly-attached configs; here we point each agent
    #    at the live config it owns).
    op.execute(
        "UPDATE agents SET graphrag_config_id = ("
        "SELECT c.id FROM graphrag_configs c "
        "WHERE c.agent_id = agents.id AND c.deleted_at IS NULL LIMIT 1)"
    )

    # 4. Relax the owner discriminator back to the expand-phase shape.
    op.drop_constraint("ck_graphrag_configs_owner", "graphrag_configs", type_="check")
    op.alter_column("graphrag_configs", "owner_kind", nullable=True)
