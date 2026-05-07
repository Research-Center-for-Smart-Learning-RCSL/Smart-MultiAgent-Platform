"""SQLAlchemy Core tables for the GraphRAG context.

DDL is owned by ``alembic/versions/0013_graphrag.py``. This module exists
so the shared_kernel db registry can import the table binding.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

graphrag_configs = sa.Table(
    "graphrag_configs",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "agent_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "builder_key_group_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("key_groups.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("trigger_config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("last_build_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column(
        "last_build_state",
        pg.ENUM(
            "idle",
            "running",
            "neo4j_committed",
            "qdrant_committed",
            "failed_compensating",
            "failed",
            name="graphrag_build_state",
            create_type=False,
        ),
        nullable=False,
        server_default=sa.text("'idle'::graphrag_build_state"),
    ),
    sa.Column("last_build_error", sa.Text, nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


__all__ = ["graphrag_configs"]
