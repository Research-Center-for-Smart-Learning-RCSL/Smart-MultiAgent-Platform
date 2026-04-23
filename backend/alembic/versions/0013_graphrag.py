"""graphrag_configs + deferred agents.graphrag_config_id FK — E.7 / §11 / R11.01–R11.06.

GraphRAG is configured per-Agent (1:1 per R11.05): each config owns a
builder Key Group (distinct from the consumer agent's Key Group, per
R11.01), a ``trigger_config`` JSONB mirroring wakeup triggers, and a
small 2PC state machine tracking the last build (R11.04 / §11.2a).

Also installs the deferred FK ``agents.graphrag_config_id → graphrag_configs.id``
that 0011 could not declare because the target table did not yet exist —
this mirrors the pattern used for ``agents.rag_config_id`` in 0012.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0013_graphrag"
down_revision: str | Sequence[str] | None = "0012_rag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BUILD_STATES: tuple[str, ...] = (
    "idle",
    "running",
    "neo4j_committed",
    "qdrant_committed",
    "failed_compensating",
    "failed",
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE graphrag_build_state AS ENUM ("
        + ", ".join(f"'{v}'" for v in _BUILD_STATES)
        + ")"
    )

    op.create_table(
        "graphrag_configs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"),
                  nullable=False),
        # R11.05 — 1:1 with an agent.
        sa.Column("agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        # R11.01 — builder Key Group distinct from the agent's consumer group.
        sa.Column("builder_key_group_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("key_groups.id", ondelete="RESTRICT"),
                  nullable=False),
        # R11.02 — mirror of wakeup_config but counts ALL messages.
        sa.Column("trigger_config", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_build_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_build_state",
            pg.ENUM(*_BUILD_STATES, name="graphrag_build_state",
                    create_type=False),
            nullable=False,
            server_default=sa.text("'idle'::graphrag_build_state"),
        ),
        sa.Column("last_build_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_graphrag_configs_project", "graphrag_configs", ["project_id"]
    )

    # Late-bind the FK the 0011 migration could not write.
    op.create_foreign_key(
        "fk_agents_graphrag_config",
        source_table="agents",
        referent_table="graphrag_configs",
        local_cols=["graphrag_config_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agents_graphrag_config", "agents", type_="foreignkey"
    )
    op.drop_index(
        "ix_graphrag_configs_project", table_name="graphrag_configs"
    )
    op.drop_table("graphrag_configs")
    op.execute("DROP TYPE graphrag_build_state")
