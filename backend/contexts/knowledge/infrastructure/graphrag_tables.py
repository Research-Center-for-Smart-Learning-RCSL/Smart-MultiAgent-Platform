"""SQLAlchemy Core tables for the GraphRAG context.

DDL is owned by ``alembic/versions/0013_graphrag.py``, amended by ``0058`` (which
moved ``last_build_state`` off the shared PG ENUM onto Text + CHECK). This module
exists so the shared_kernel db registry can import the table binding.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from contexts.knowledge.domain.graphrag import build_state_check_sql
from shared_kernel.db import metadata

graphrag_configs = sa.Table(
    "graphrag_configs",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "builder_key_group_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("key_groups.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("trigger_config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("last_build_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # F-4 (migration 0059): started-at watermark for the *current* build. Distinct
    # from last_build_at, which is only written on a terminal outcome and so still
    # holds the previous build's time while this one runs. Observability only.
    sa.Column("build_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # Text + CHECK, not a PG ENUM (migration 0058): the state set grows, and this
    # backend has no ALTER TYPE ... ADD VALUE precedent. The CHECK is named with the
    # short form so shared_kernel/db's ck_%(table_name)s_%(constraint_name)s
    # convention renders exactly the literal 0058 creates.
    sa.Column(
        "last_build_state",
        sa.Text,
        nullable=False,
        server_default=sa.text("'idle'"),
    ),
    sa.CheckConstraint(build_state_check_sql(), name="build_state_valid"),
    sa.Column("last_build_error", sa.Text, nullable=True),
    # Embedding pin (Phase 2a D2, migration 0045). One (provider, model, dim)
    # per project so every config shares the per-project Qdrant collection at a
    # single stable vector dimension. Nullable: pre-2a rows self-pin on their
    # next successful build. Plain Text/Integer — no PG ENUM (ORM enum-match rule).
    sa.Column("embed_provider", sa.Text, nullable=True),
    sa.Column("embed_model", sa.Text, nullable=True),
    sa.Column("embed_dim", sa.Integer, nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    # Phase 2b WS5 (R11.21, migration 0047): per-config recency half-life in days
    # for temporal retrieval decay. Nullable — NULL inherits the platform default
    # setting. Plain Float, matching the migration's DOUBLE PRECISION.
    sa.Column("recency_half_life_days", sa.Float, nullable=True),
    # Discriminated owner (Phase 1, R11.05/R11.07/R11.08). The contract migration
    # (0044) made owner_kind NOT NULL, added the exactly-one-owner CHECK, and
    # dropped the legacy ``agent_id`` anchor. The owning agent (when the owner is
    # an agent_group) is derived from the group's membership at read time.
    sa.Column(
        "owner_kind",
        pg.ENUM("chatroom", "agent_group", "workspace", name="owner_kind", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "owner_chatroom_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column(
        "owner_agent_group_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("agent_groups.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column(
        "owner_workspace_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    ),
)


__all__ = ["graphrag_configs"]
