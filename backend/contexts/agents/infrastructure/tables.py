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
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column(
        "model_hint",
        pg.ENUM("claude", "openai", "gemini", name="agent_model_hint", create_type=False),
        nullable=False,
    ),
    sa.Column(
        "key_group_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("key_groups.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    sa.Column("model_id", sa.Text, nullable=True),
    sa.Column("system_prompt", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column(
        "prompt_strategy",
        pg.ENUM("full", "lazy", name="agent_prompt_strategy", create_type=False),
        nullable=False,
        server_default=sa.text("'full'::agent_prompt_strategy"),
    ),
    sa.Column("rag_config_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("graphrag_config_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column(
        "context_mode",
        pg.ENUM("general", "compact", name="agent_context_mode", create_type=False),
        nullable=False,
        server_default=sa.text("'general'::agent_context_mode"),
    ),
    sa.Column("context_token_cap", sa.Integer, nullable=True),
    sa.Column("a2a_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("wakeup_config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("wakeup_authored_snapshot", pg.JSONB, nullable=True),
    sa.Column("workflow_capabilities", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)


agent_tools = sa.Table(
    "agent_tools",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "tool_type",
        pg.ENUM(
            "hosted_mcp",
            "hosted_web_search",
            "hosted_code_interpreter",
            "hosted_file_workspace",
            "hosted_file_search",
            "local_function",
            "local_shell",
            name="agent_tool_type",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("display_name", sa.Text, nullable=True),
    sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)


agent_workspace_files = sa.Table(
    "agent_workspace_files",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False),
    sa.Column("mime", sa.Text, nullable=False),
    sa.Column("minio_key", sa.Text, nullable=False),
    sa.Column(
        "created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("agent_id", "path", name="uq_agent_workspace_files_agent_path"),
)


__all__ = ["agent_tools", "agent_workspace_files", "agents"]
