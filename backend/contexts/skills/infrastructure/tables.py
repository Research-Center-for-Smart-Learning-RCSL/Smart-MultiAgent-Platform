"""SQLAlchemy Core tables for §31 Agent Skills.

DDL is owned by ``alembic/versions/0056_skills.py``; these bindings must agree with it
column for column. The enum bindings in particular are load-bearing: a PG ENUM column
bound as ``sa.Text`` (or bound to a type whose value set has drifted) fails at asyncpg
bind time as a 500, not at startup.

``skill_files.kind`` is Text + CHECK rather than an ENUM — the value set will grow and
this backend has no ``ALTER TYPE ... ADD VALUE`` precedent. ``scan_status`` reuses the
shared ``rag_scan_status`` type (0012_rag), as 0048 did.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from shared_kernel.db import metadata

_SCOPES: tuple[str, ...] = ("agent", "project", "org", "platform")
_SOURCES: tuple[str, ...] = ("authored", "imported")
_SCAN_STATUSES: tuple[str, ...] = ("pending", "clean", "quarantined", "skipped")

skills = sa.Table(
    "skills",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column("scope", pg.ENUM(*_SCOPES, name="skill_scope", create_type=False), nullable=False),
    sa.Column(
        "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
    ),
    sa.Column(
        "project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    ),
    sa.Column("org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("description", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("body", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("body_sha256", sa.String(64), nullable=False),
    sa.Column("source", pg.ENUM(*_SOURCES, name="skill_source", create_type=False), nullable=False),
    sa.Column("bundle_sha256", sa.String(64), nullable=True),
    sa.Column("requires", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")),
    sa.Column("allowed_tools", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")),
    sa.Column("extra_frontmatter", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    ),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

skill_files = sa.Table(
    "skill_files",
    metadata,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
    sa.Column(
        "skill_id", pg.UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("mime", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("sha256", sa.String(64), nullable=False),
    sa.Column("minio_key", sa.Text, nullable=False),
    sa.Column(
        "scan_status",
        pg.ENUM(*_SCAN_STATUSES, name="rag_scan_status", create_type=False),
        nullable=False,
        server_default=sa.text("'pending'::rag_scan_status"),
    ),
    sa.Column("extracted_chars", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("skill_id", "path", name="uq_skill_files_skill_path"),
)

agent_skills = sa.Table(
    "agent_skills",
    metadata,
    # Bare UUID, no sa.ForeignKey: cross-context, validated through AgentsFacade rather
    # than the schema. The DB-level FK exists (0056) as a physical-delete backstop only.
    # This mirrors agents.rag_config_id / knowmap_config_id, the precedent this context's
    # ADR rests on — declaring the FK here would issue the cross-context join [R23.01]
    # forbids.
    sa.Column("agent_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column(
        "skill_id", pg.UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    ),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("cascade_deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("agent_id", "skill_id", name="pk_agent_skills"),
)


__all__ = ["agent_skills", "skill_files", "skills"]
