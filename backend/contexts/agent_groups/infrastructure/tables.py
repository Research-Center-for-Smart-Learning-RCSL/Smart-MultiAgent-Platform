"""SQLAlchemy Core tables for the agent-groups context (R24.06).

A group is the discriminated owner substrate the GraphRAG (and later Knowledge
Map) configs point at instead of a single agent. In Phase 1 groups are internal
singletons auto-managed per former owning agent; public CRUD + multi-member
management is Phase 2b/4.

DDL is owned by ``alembic/versions/0043_graphrag_owner.py``. This module exists
so the shared_kernel db registry can import the table bindings.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

agent_groups = sa.Table(
    "agent_groups",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("name", sa.Text, nullable=False),
    # Phase 2b WS3 (R11.10/R11.17, migration 0046): strict-by-default privacy
    # opt-in. A wide (agent_group) Concept Map contributes nothing to retrieval
    # until a Project Owner enables it. Bool NOT NULL — matches the migration
    # server_default false (ORM enum/type-match rule).
    sa.Column(
        "concept_map_enabled",
        sa.Boolean,
        nullable=False,
        server_default=sa.text("false"),
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

agent_group_members = sa.Table(
    "agent_group_members",
    metadata,
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


__all__ = ["agent_group_members", "agent_groups"]
