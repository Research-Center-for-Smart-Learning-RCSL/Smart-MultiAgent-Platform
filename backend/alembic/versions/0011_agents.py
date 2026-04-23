"""agents + agent_mcp_servers — E.1 / §9.1 / R9.01–R9.03.

Agents are **not versioned** (no `agent_versions` table) and never templated.
Edits overwrite in place guarded by optimistic-lock column `version`.
Soft-delete with 60-day recovery is implemented purely via `deleted_at`;
the nightly reaper lives in Phase I.

The partial-unique index `uq_agents_project_name_active` prevents a live
agent from colliding with another live agent of the same name in the same
project, while letting soft-deleted rows coexist with their eventual
successor — consistent with tenancy's own `uq_projects_*` treatment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0011_agents"
down_revision: str | Sequence[str] | None = "0010_rewrap_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_MODEL_HINTS: tuple[str, ...] = ("claude", "openai", "gemini")
_PROMPT_STRATEGIES: tuple[str, ...] = ("full", "lazy")
_CONTEXT_MODES: tuple[str, ...] = ("general", "compact")
_MCP_SOURCES: tuple[str, ...] = ("builtin", "url", "package")


def upgrade() -> None:
    op.execute(
        "CREATE TYPE agent_model_hint AS ENUM ("
        + ", ".join(f"'{v}'" for v in _MODEL_HINTS)
        + ")"
    )
    op.execute(
        "CREATE TYPE agent_prompt_strategy AS ENUM ("
        + ", ".join(f"'{v}'" for v in _PROMPT_STRATEGIES)
        + ")"
    )
    op.execute(
        "CREATE TYPE agent_context_mode AS ENUM ("
        + ", ".join(f"'{v}'" for v in _CONTEXT_MODES)
        + ")"
    )
    op.execute(
        "CREATE TYPE agent_mcp_source AS ENUM ("
        + ", ".join(f"'{v}'" for v in _MCP_SOURCES)
        + ")"
    )

    op.create_table(
        "agents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("model_hint",
                  pg.ENUM(*_MODEL_HINTS, name="agent_model_hint", create_type=False),
                  nullable=False),
        sa.Column("key_group_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("key_groups.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False,
                  server_default=sa.text("''")),
        sa.Column("prompt_strategy",
                  pg.ENUM(*_PROMPT_STRATEGIES,
                          name="agent_prompt_strategy", create_type=False),
                  nullable=False,
                  server_default=sa.text("'full'::agent_prompt_strategy")),
        # FKs to rag_configs / graphrag_configs are added in later migrations
        # once those tables exist; for now they are plain nullable UUIDs.
        sa.Column("rag_config_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("graphrag_config_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("context_mode",
                  pg.ENUM(*_CONTEXT_MODES,
                          name="agent_context_mode", create_type=False),
                  nullable=False,
                  server_default=sa.text("'general'::agent_context_mode")),
        sa.Column("context_token_cap", sa.Integer(), nullable=True),
        sa.Column("a2a_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("wakeup_config", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("workflow_capabilities", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "context_token_cap IS NULL OR context_token_cap > 0",
            name="agents_context_cap_positive",
        ),
    )
    op.create_index("ix_agents_project", "agents", ["project_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_agents_project_name_active "
        "ON agents (project_id, name) WHERE deleted_at IS NULL"
    )

    op.create_table(
        "agent_mcp_servers",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source",
                  pg.ENUM(*_MCP_SOURCES,
                          name="agent_mcp_source", create_type=False),
                  nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("allowed_tools", pg.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("config", pg.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_agent_mcp_servers_agent", "agent_mcp_servers", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_mcp_servers_agent", table_name="agent_mcp_servers")
    op.drop_table("agent_mcp_servers")
    op.execute("DROP INDEX IF EXISTS uq_agents_project_name_active")
    op.drop_index("ix_agents_project", table_name="agents")
    op.drop_table("agents")
    op.execute("DROP TYPE agent_mcp_source")
    op.execute("DROP TYPE agent_context_mode")
    op.execute("DROP TYPE agent_prompt_strategy")
    op.execute("DROP TYPE agent_model_hint")
