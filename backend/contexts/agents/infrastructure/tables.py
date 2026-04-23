"""SQLAlchemy Core tables for the agents context.

DDL is owned by `alembic/versions/0011_agents.py`. This module exists so the
shared_kernel `db.registry` pulls the table bindings into the global MetaData
at import time and so repositories can target typed columns.

SoC: the domain layer never imports this module.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

agents = sa.Table(
    "agents",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("project_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("model_hint",
              pg.ENUM("claude", "openai", "gemini",
                      name="agent_model_hint", create_type=False),
              nullable=False),
    sa.Column("key_group_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("key_groups.id", ondelete="RESTRICT"), nullable=False),
    sa.Column("system_prompt", sa.Text, nullable=False,
              server_default=sa.text("''")),
    sa.Column("prompt_strategy",
              pg.ENUM("full", "lazy",
                      name="agent_prompt_strategy", create_type=False),
              nullable=False,
              server_default=sa.text("'full'::agent_prompt_strategy")),
    sa.Column("rag_config_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("graphrag_config_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("context_mode",
              pg.ENUM("general", "compact",
                      name="agent_context_mode", create_type=False),
              nullable=False,
              server_default=sa.text("'general'::agent_context_mode")),
    sa.Column("context_token_cap", sa.Integer, nullable=True),
    sa.Column("a2a_enabled", sa.Boolean, nullable=False,
              server_default=sa.text("false")),
    sa.Column("wakeup_config", pg.JSONB, nullable=False,
              server_default=sa.text("'{}'::jsonb")),
    sa.Column("wakeup_authored_snapshot", pg.JSONB, nullable=True),
    sa.Column("workflow_capabilities", pg.JSONB, nullable=False,
              server_default=sa.text("'{}'::jsonb")),
    sa.Column("version", sa.Integer, nullable=False,
              server_default=sa.text("1")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)


agent_mcp_servers = sa.Table(
    "agent_mcp_servers",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
              server_default=sa.text("gen_random_uuid()")),
    sa.Column("agent_id", pg.UUID(as_uuid=True),
              sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
    sa.Column("source",
              pg.ENUM("builtin", "url", "package",
                      name="agent_mcp_source", create_type=False),
              nullable=False),
    sa.Column("reference", sa.Text, nullable=False),
    sa.Column("allowed_tools", pg.ARRAY(sa.Text), nullable=False,
              server_default=sa.text("'{}'::text[]")),
    sa.Column("config", pg.JSONB, nullable=False,
              server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
              server_default=sa.text("now()")),
)


__all__ = ["agent_mcp_servers", "agents"]
