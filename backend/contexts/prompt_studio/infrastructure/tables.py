"""SQLAlchemy Core tables for the prompt_studio context.

DDL is owned by ``alembic/versions/0042_prompt_studio.py``; this module exists
so application queries can target typed columns and so ``app.db_registry`` can
import the bindings on boot. PG ENUM types are created ``create_type=False``
here and mirrored exactly by the migration (memory rule: ORM enum type match).

SoC: the domain layer does not import this file.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

_SCOPE_ENUM = pg.ENUM(
    "platform",
    "org",
    "user",
    name="prompt_studio_scope",
    create_type=False,
)

_SCAN_STATUS_ENUM = pg.ENUM(
    "pending",
    "clean",
    "infected",
    "error",
    name="prompt_file_scan_status",
    create_type=False,
)


prompt_assistant_configs = sa.Table(
    "prompt_assistant_configs",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("scope", _SCOPE_ENUM, nullable=False),
    sa.Column("org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True),
    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    sa.Column("system_prompt", sa.Text, nullable=False, server_default=sa.text("''")),
    # Pinned provider key, owned by the configurer. SET NULL on hard-delete so a
    # revoked key leaves a resolvable-but-broken config the UI can flag, rather
    # than cascading the whole config away.
    sa.Column(
        "key_id", pg.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    ),
    sa.Column("model_id", sa.Text, nullable=True),
    sa.Column("daily_request_limit_per_user", sa.Integer, nullable=False, server_default=sa.text("50")),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
    # Meaningful only for scope='org' (OrgTemplateVisibility per §6): hide
    # platform templates from this org's projects' pickers.
    sa.Column("hide_platform_templates", sa.Boolean, nullable=False, server_default=sa.text("false")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.CheckConstraint(
        "(scope = 'platform' AND org_id IS NULL AND user_id IS NULL) "
        "OR (scope = 'org' AND org_id IS NOT NULL AND user_id IS NULL) "
        "OR (scope = 'user' AND user_id IS NOT NULL AND org_id IS NULL)",
        name="ck_prompt_assistant_config_scope",
    ),
    sa.CheckConstraint(
        "daily_request_limit_per_user > 0",
        name="ck_prompt_assistant_config_cap_positive",
    ),
)


prompt_assistant_files = sa.Table(
    "prompt_assistant_files",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "config_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("prompt_assistant_configs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False),
    sa.Column("mime", sa.Text, nullable=False),
    sa.Column("minio_key", sa.Text, nullable=False),
    sa.Column(
        "scan_status",
        _SCAN_STATUS_ENUM,
        nullable=False,
        server_default=sa.text("'pending'::prompt_file_scan_status"),
    ),
    sa.Column("extracted_chars", sa.Integer, nullable=False, server_default=sa.text("0")),
    # Extracted UTF-8 text inlined into the assistant context at turn time.
    # Bounded by the 200 KB per-config budget, so storing it in-row is cheap
    # and avoids re-parsing / a MinIO round-trip on every assistant turn.
    sa.Column("extracted_text", sa.Text, nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
)


prompt_templates = sa.Table(
    "prompt_templates",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("scope", _SCOPE_ENUM, nullable=False),
    sa.Column("org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True),
    sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("body", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.CheckConstraint(
        "(scope = 'platform' AND org_id IS NULL AND user_id IS NULL) "
        "OR (scope = 'org' AND org_id IS NOT NULL AND user_id IS NULL) "
        "OR (scope = 'user' AND user_id IS NOT NULL AND org_id IS NULL)",
        name="ck_prompt_template_scope",
    ),
)


__all__ = [
    "prompt_assistant_configs",
    "prompt_assistant_files",
    "prompt_templates",
]
