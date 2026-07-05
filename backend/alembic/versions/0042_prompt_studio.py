"""prompt_studio + key_usage_events.usage_context — §29 / R29.01-R29.14.

Adds the prompt assistant + prompt template aggregates at platform / org / user
scope, plus a nullable ``usage_context`` discriminator on ``key_usage_events`` so
assistant LLM calls (which carry NULL agent/chatroom attribution, like GraphRAG
and embed traffic) can be told apart in usage reporting (AC-8).

Fully additive and reversible: no data backfill; the new column defaults NULL so
every existing call site records unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0042_prompt_studio"
down_revision: str | Sequence[str] | None = "0041_observer_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SCOPES: tuple[str, ...] = ("platform", "org", "user")
_SCAN_STATUSES: tuple[str, ...] = ("pending", "clean", "infected", "error")

_SCOPE_CHECK = (
    "(scope = 'platform' AND org_id IS NULL AND user_id IS NULL) "
    "OR (scope = 'org' AND org_id IS NOT NULL AND user_id IS NULL) "
    "OR (scope = 'user' AND user_id IS NOT NULL AND org_id IS NULL)"
)


def upgrade() -> None:
    # --- usage discriminator on the hot accounting table (additive, NULL default)
    op.add_column(
        "key_usage_events",
        sa.Column("usage_context", sa.Text(), nullable=True),
    )

    op.execute("CREATE TYPE prompt_studio_scope AS ENUM (" + ", ".join(f"'{v}'" for v in _SCOPES) + ")")
    op.execute(
        "CREATE TYPE prompt_file_scan_status AS ENUM (" + ", ".join(f"'{v}'" for v in _SCAN_STATUSES) + ")"
    )

    op.create_table(
        "prompt_assistant_configs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope", pg.ENUM(*_SCOPES, name="prompt_studio_scope", create_type=False), nullable=False),
        sa.Column(
            "org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "key_id", pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("daily_request_limit_per_user", sa.Integer(), nullable=False, server_default=sa.text("50")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("hide_platform_templates", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(_SCOPE_CHECK, name="ck_prompt_assistant_config_scope"),
        sa.CheckConstraint(
            "daily_request_limit_per_user > 0", name="ck_prompt_assistant_config_cap_positive"
        ),
    )
    # At most one config per scope holder: one platform-wide row, one per org, one
    # per user. Partial unique indexes let the three scopes share a table.
    op.execute(
        "CREATE UNIQUE INDEX uq_prompt_assistant_config_platform "
        "ON prompt_assistant_configs (scope) WHERE scope = 'platform'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_prompt_assistant_config_org "
        "ON prompt_assistant_configs (org_id) WHERE scope = 'org'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_prompt_assistant_config_user "
        "ON prompt_assistant_configs (user_id) WHERE scope = 'user'"
    )

    op.create_table(
        "prompt_assistant_files",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "config_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("prompt_assistant_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("minio_key", sa.Text(), nullable=False),
        sa.Column(
            "scan_status",
            pg.ENUM(*_SCAN_STATUSES, name="prompt_file_scan_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'::prompt_file_scan_status"),
        ),
        sa.Column("extracted_chars", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_prompt_assistant_files_config", "prompt_assistant_files", ["config_id"])

    op.create_table(
        "prompt_templates",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope", pg.ENUM(*_SCOPES, name="prompt_studio_scope", create_type=False), nullable=False),
        sa.Column(
            "org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(_SCOPE_CHECK, name="ck_prompt_template_scope"),
    )
    op.create_index("ix_prompt_templates_org", "prompt_templates", ["org_id"])
    op.create_index("ix_prompt_templates_user", "prompt_templates", ["user_id"])
    op.create_index("ix_prompt_templates_scope", "prompt_templates", ["scope"])


def downgrade() -> None:
    op.drop_index("ix_prompt_templates_scope", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_user", table_name="prompt_templates")
    op.drop_index("ix_prompt_templates_org", table_name="prompt_templates")
    op.drop_table("prompt_templates")

    op.drop_index("ix_prompt_assistant_files_config", table_name="prompt_assistant_files")
    op.drop_table("prompt_assistant_files")

    op.execute("DROP INDEX IF EXISTS uq_prompt_assistant_config_user")
    op.execute("DROP INDEX IF EXISTS uq_prompt_assistant_config_org")
    op.execute("DROP INDEX IF EXISTS uq_prompt_assistant_config_platform")
    op.drop_table("prompt_assistant_configs")

    op.execute("DROP TYPE prompt_file_scan_status")
    op.execute("DROP TYPE prompt_studio_scope")

    op.drop_column("key_usage_events", "usage_context")
