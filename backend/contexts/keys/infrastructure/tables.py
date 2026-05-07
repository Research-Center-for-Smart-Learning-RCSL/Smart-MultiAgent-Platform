"""SQLAlchemy Core tables for the keys context.

DDL is owned by `alembic/versions/0005_api_keys.py`; this module exists so
application queries can target typed columns and so `shared_kernel.db.registry`
can import the table bindings on boot.

SoC: domain layer does not import this file.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

api_keys = sa.Table(
    "api_keys",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "owner_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "provider",
        pg.ENUM(
            "claude",
            "openai",
            "gemini",
            "voyage",
            "cohere",
            name="api_key_provider",
            create_type=False,
        ),
        nullable=False,
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("dek_wrapped", sa.Text, nullable=False),
    sa.Column("ciphertext_hmac", sa.LargeBinary, nullable=False),
    sa.Column("transit_key_version", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("hmac_key_version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("masked_preview", sa.Text, nullable=False),
    sa.Column(
        "test_status",
        pg.ENUM(
            "ok",
            "failed",
            "untested",
            name="key_test_status",
            create_type=False,
        ),
        nullable=False,
        server_default=sa.text("'untested'::key_test_status"),
    ),
    sa.Column("test_error", sa.Text, nullable=True),
    sa.Column("last_test_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


key_projects = sa.Table(
    "key_projects",
    metadata,
    sa.Column(
        "key_id", pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column(
        "project_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("carried", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column(
        "added_by_user_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("added_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("withdrawn_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


key_groups = sa.Table(
    "key_groups",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

key_group_members = sa.Table(
    "key_group_members",
    metadata,
    sa.Column(
        "group_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("key_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column(
        "key_id", pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column("priority", sa.Integer, nullable=False),
    sa.Column(
        "rotate_on_error_codes",
        pg.ARRAY(sa.Integer),
        nullable=False,
        server_default=sa.text("'{429,500,502,503}'::int[]"),
    ),
    sa.Column("rotate_on_token_quota", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("retry_on_error", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("retry_initial_delay_ms", sa.Integer, nullable=False, server_default=sa.text("500")),
    sa.Column("retry_multiplier", sa.Numeric(5, 2), nullable=False, server_default=sa.text("2.0")),
    sa.Column("retry_max_delay_ms", sa.Integer, nullable=False, server_default=sa.text("30000")),
    sa.Column("retry_max", sa.Integer, nullable=False, server_default=sa.text("3")),
    sa.Column("retry_jitter_pct", sa.Integer, nullable=False, server_default=sa.text("20")),
    sa.Column("max_input_tokens_per_hour", sa.BigInteger, nullable=True),
    sa.Column("max_output_tokens_per_hour", sa.BigInteger, nullable=True),
    sa.Column("max_requests_per_hour", sa.Integer, nullable=True),
)


key_usage_events = sa.Table(
    "key_usage_events",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column(
        "key_id", pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("agent_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("parent_agent_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("chatroom_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("input_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("output_tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("request_ms", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("http_status", sa.Integer, nullable=True),
    sa.Column("error_code", sa.Text, nullable=True),
    sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)

key_usage_daily = sa.Table(
    "key_usage_daily",
    metadata,
    sa.Column(
        "key_id", pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    ),
    sa.Column("day", sa.Date, primary_key=True),
    sa.Column("input_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("output_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("requests", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("errors", sa.Integer, nullable=False, server_default=sa.text("0")),
)


rewrap_progress = sa.Table(
    "rewrap_progress",
    metadata,
    sa.Column("table_name", sa.Text, primary_key=True),
    sa.Column("last_id", pg.UUID(as_uuid=True), nullable=True),
    sa.Column("target_transit_version", sa.Integer, nullable=False),
    sa.Column("rows_rewrapped", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

search_keys = sa.Table(
    "search_keys",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column(
        "provider",
        pg.ENUM("brave", "serper", "tavily", "google_cse", name="search_provider", create_type=False),
        nullable=False,
    ),
    sa.Column("ciphertext", sa.LargeBinary, nullable=False),
    sa.Column("nonce", sa.LargeBinary, nullable=False),
    sa.Column("dek_wrapped", sa.Text, nullable=False),
    sa.Column("ciphertext_hmac", sa.LargeBinary, nullable=False),
    sa.Column("transit_key_version", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("hmac_key_version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("masked_preview", sa.Text, nullable=False),
    sa.Column(
        "test_status",
        pg.ENUM("ok", "failed", "untested", name="key_test_status", create_type=False),
        nullable=False,
        server_default=sa.text("'untested'::key_test_status"),
    ),
    sa.Column("test_error", sa.Text, nullable=True),
    sa.Column("last_test_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("config", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)


__all__ = [
    "api_keys",
    "key_group_members",
    "key_groups",
    "key_projects",
    "key_usage_daily",
    "key_usage_events",
    "rewrap_progress",
    "search_keys",
]
