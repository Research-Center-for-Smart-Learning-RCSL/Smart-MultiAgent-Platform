"""Scope the graphrag owner unique indexes to live rows.

``0043_graphrag_owner`` created the three owner indexes as
``WHERE {col} IS NOT NULL``, so a soft-deleted config kept holding its owner
slot forever. The repository worked around that by nulling all three owner
columns on soft delete, which ``0044_graphrag_drop_agent_id``'s
exactly-one-owner CHECK then rejected outright.

Adding ``AND deleted_at IS NULL`` removes the need for the workaround: the
owner slot frees on delete while the row keeps a valid owner, so the CHECK
holds and a project restore can bring the config back intact.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0061_graphrag_owner_index_live_only"
down_revision: str | Sequence[str] | None = "0060_cascade_deleted_projects_to_graph_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER_KINDS: tuple[str, ...] = ("chatroom", "agent_group", "workspace")


def upgrade() -> None:
    for kind in _OWNER_KINDS:
        col = f"owner_{kind}_id"
        op.execute(f"DROP INDEX IF EXISTS uq_graphrag_configs_owner_{kind}")
        op.execute(
            f"CREATE UNIQUE INDEX uq_graphrag_configs_owner_{kind} "
            f"ON graphrag_configs ({col}) WHERE {col} IS NOT NULL AND deleted_at IS NULL"
        )


def downgrade() -> None:
    # The unscoped index cannot represent a deleted config whose owner a live
    # config now holds — the state this migration made reachable. Nulling the
    # owner instead would break 0044's CHECK, so the only way back is to drop
    # those shadowed rows; the live config for that owner is the survivor.
    for kind in _OWNER_KINDS:
        col = f"owner_{kind}_id"
        # col is interpolated from the _OWNER_KINDS literal above, never input.
        sql = (
            "DELETE FROM graphrag_configs d WHERE d.deleted_at IS NOT NULL "  # noqa: S608
            f"AND d.{col} IS NOT NULL AND EXISTS ("
            f"SELECT 1 FROM graphrag_configs o WHERE o.{col} = d.{col} "
            "AND o.id <> d.id AND (o.deleted_at IS NULL "
            "OR (o.created_at, o.id) > (d.created_at, d.id)))"
        )
        op.execute(sql)
    for kind in _OWNER_KINDS:
        col = f"owner_{kind}_id"
        op.execute(f"DROP INDEX IF EXISTS uq_graphrag_configs_owner_{kind}")
        op.execute(
            f"CREATE UNIQUE INDEX uq_graphrag_configs_owner_{kind} "
            f"ON graphrag_configs ({col}) WHERE {col} IS NOT NULL"
        )
