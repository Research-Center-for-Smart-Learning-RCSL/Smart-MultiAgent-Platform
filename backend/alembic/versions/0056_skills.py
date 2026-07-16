"""skills + skill_files + agent_skills; agents.skill_index_token_cap — §31.

Agent Skills (task dossier: docs/tasks/2026-07-16-agent-skills/). A Skill is a named,
described, reusable instruction bundle owned at one of four scopes — agent / project /
org / platform — bound to agents explicitly, with only an index of names and
descriptions riding in the system prompt.

Mints two ENUMs (``skill_scope`` / ``skill_source``) and **reuses** ``rag_scan_status``
(0012_rag), as 0048 did — do not mint a third scan-status type. The downgrade drops only
what this migration created, never the shared type.

``skill_files.kind`` is ``sa.Text`` + CHECK rather than a PG ENUM: the value set will
grow, and this backend has **no** ``ALTER TYPE ... ADD VALUE`` precedent anywhere. The
shape copied is ``project_embedding_pins.kind`` (0052 / embedding_pin_tables.py) — a
column literally named ``kind``, Text, constrained by a CHECK.

Four partial unique indexes, not one: NULLs compare distinct, so a single index over the
owner columns would give ``platform`` scope (all owners NULL) no uniqueness at all. The
precedent is 0042's per-scope indexes on ``prompt_assistant_configs``.

**The FK CASCADEs here are a backstop, not the mechanism.** Agents, projects, and orgs
all soft-delete, and a FK never fires on an UPDATE — the trap 0054 exists to repair.
Owner soft-delete must unbind explicitly, inside the owner's own transaction; these
CASCADEs only catch the 60-day reaper's physical DELETE.

Also drops ``agents.prompt_strategy`` and ``agent_prompt_strategy`` (§9.2, superseded by
this chapter). Zero rows used ``lazy``, so the downgrade — which re-creates both with the
``'full'`` server default — restores a behaviourally identical schema.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0056_skills"
down_revision: str | Sequence[str] | None = "0055_document_ingest_attempt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SCOPES: tuple[str, ...] = ("agent", "project", "org", "platform")
_SOURCES: tuple[str, ...] = ("authored", "imported")
_FILE_KINDS: tuple[str, ...] = ("reference", "script", "asset")

# Values of the existing rag_scan_status type (0012_rag), reused verbatim.
_SCAN_STATUSES: tuple[str, ...] = ("pending", "clean", "quarantined", "skipped")

# Exactly one owner column is populated, and which one is fixed by the scope.
# `platform` owns nothing. Mirrors 0042's ck_prompt_*_scope XOR, extended to four arms.
_SCOPE_CHECK = (
    "(scope = 'agent' AND agent_id IS NOT NULL AND project_id IS NULL AND org_id IS NULL) "
    "OR (scope = 'project' AND project_id IS NOT NULL AND agent_id IS NULL AND org_id IS NULL) "
    "OR (scope = 'org' AND org_id IS NOT NULL AND agent_id IS NULL AND project_id IS NULL) "
    "OR (scope = 'platform' AND agent_id IS NULL AND project_id IS NULL AND org_id IS NULL)"
)

# Upper-bounded, unlike context_token_cap (0011_agents.py:97), which is unbounded above
# and is a self-DoS once the index counts against the knowledge budget.
_INDEX_CAP_CHECK = (
    "skill_index_token_cap IS NULL OR "
    "(skill_index_token_cap > 0 AND skill_index_token_cap <= 16000)"
)

_PROMPT_STRATEGIES: tuple[str, ...] = ("full", "lazy")


def upgrade() -> None:
    skill_scope = pg.ENUM(*_SCOPES, name="skill_scope")
    skill_source = pg.ENUM(*_SOURCES, name="skill_source")
    bind = op.get_bind()
    skill_scope.create(bind, checkfirst=True)
    skill_source.create(bind, checkfirst=True)

    op.create_table(
        "skills",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope", pg.ENUM(*_SCOPES, name="skill_scope", create_type=False), nullable=False),
        sa.Column(
            "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "org_id", pg.UUID(as_uuid=True), sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("body", sa.Text(), nullable=False, server_default=sa.text("''")),
        # Recorded per read and per update so "which bytes executed" stays answerable
        # even though bodies are mutable in place and there is no version tree (Q-21/Q-27).
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("source", pg.ENUM(*_SOURCES, name="skill_source", create_type=False), nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=True),
        # Derived from bundle content unioned with any declaration — never the
        # declaration alone, which would make the gate cooperative (Q-9).
        sa.Column("requires", pg.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column(
            "allowed_tools", pg.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        # Q-29's tolerated-unknown frontmatter keys, preserved verbatim for round-trip.
        sa.Column("extra_frontmatter", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(_SCOPE_CHECK, name="ck_skill_scope"),
    )

    # One live name per scope holder. Four indexes because NULLs compare distinct:
    # a single (agent_id, project_id, org_id, name) index would let every platform
    # skill duplicate freely. `prompt_templates` has no name uniqueness at all, so
    # the exemplar does not contain the thing being copied — 0042:75-88 does.
    op.execute(
        "CREATE UNIQUE INDEX uq_skills_agent_name ON skills (agent_id, name) "
        "WHERE scope = 'agent' AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_skills_project_name ON skills (project_id, name) "
        "WHERE scope = 'project' AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_skills_org_name ON skills (org_id, name) "
        "WHERE scope = 'org' AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_skills_platform_name ON skills (name) "
        "WHERE scope = 'platform' AND deleted_at IS NULL"
    )
    op.create_index("ix_skills_agent", "skills", ["agent_id"])
    op.create_index("ix_skills_project", "skills", ["project_id"])
    op.create_index("ix_skills_org", "skills", ["org_id"])

    op.create_table(
        "skill_files",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "skill_id", pg.UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("minio_key", sa.Text(), nullable=False),
        sa.Column(
            "scan_status",
            pg.ENUM(*_SCAN_STATUSES, name="rag_scan_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'::rag_scan_status"),
        ),
        sa.Column("extracted_chars", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in _FILE_KINDS) + ")",
            name="ck_skill_files_kind",
        ),
        sa.UniqueConstraint("skill_id", "path", name="uq_skill_files_skill_path"),
    )
    op.create_index("ix_skill_files_skill_sha", "skill_files", ["skill_id", "sha256"])

    op.create_table(
        "agent_skills",
        # Bare UUID + DB-level FK only (cross-context; validated in the facade, not the
        # schema) — the agents.rag_config_id / knowmap_config_id shape this context's ADR
        # rests on. No cross-context join is ever issued.
        sa.Column(
            "agent_id", pg.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "skill_id", pg.UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        # Two delete timestamps, because one flag cannot carry two meanings. `deleted_at`
        # is an explicit user unbind; `cascade_deleted_at` is unbind_all following an owner
        # soft-delete. Restore clears only the latter, so a binding a user removed on
        # purpose never comes back holding an executable body (AC-37).
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cascade_deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("agent_id", "skill_id", name="pk_agent_skills"),
    )
    op.create_index("ix_agent_skills_skill", "agent_skills", ["skill_id"])

    # Single-mechanism optimistic locking (DB-5) — never hand-increment `version`.
    op.execute(
        "CREATE TRIGGER trg_skills_bump_version "
        "BEFORE UPDATE ON skills FOR EACH ROW "
        "EXECUTE FUNCTION smap_bump_version();"
    )

    op.add_column("agents", sa.Column("skill_index_token_cap", sa.Integer(), nullable=True))
    op.create_check_constraint("agents_skill_index_cap_bounded", "agents", _INDEX_CAP_CHECK)

    # §9.2 removal. Zero rows use 'lazy'.
    op.drop_column("agents", "prompt_strategy")
    op.execute("DROP TYPE agent_prompt_strategy")


def downgrade() -> None:
    op.execute(
        "CREATE TYPE agent_prompt_strategy AS ENUM (" + ", ".join(f"'{v}'" for v in _PROMPT_STRATEGIES) + ")"
    )
    op.add_column(
        "agents",
        sa.Column(
            "prompt_strategy",
            pg.ENUM(*_PROMPT_STRATEGIES, name="agent_prompt_strategy", create_type=False),
            nullable=False,
            server_default=sa.text("'full'::agent_prompt_strategy"),
        ),
    )

    op.drop_constraint("agents_skill_index_cap_bounded", "agents", type_="check")
    op.drop_column("agents", "skill_index_token_cap")

    op.execute("DROP TRIGGER IF EXISTS trg_skills_bump_version ON skills;")

    op.drop_index("ix_agent_skills_skill", table_name="agent_skills")
    op.drop_table("agent_skills")

    op.drop_index("ix_skill_files_skill_sha", table_name="skill_files")
    op.drop_table("skill_files")

    op.drop_index("ix_skills_org", table_name="skills")
    op.drop_index("ix_skills_project", table_name="skills")
    op.drop_index("ix_skills_agent", table_name="skills")
    op.execute("DROP INDEX IF EXISTS uq_skills_platform_name")
    op.execute("DROP INDEX IF EXISTS uq_skills_org_name")
    op.execute("DROP INDEX IF EXISTS uq_skills_project_name")
    op.execute("DROP INDEX IF EXISTS uq_skills_agent_name")
    op.drop_table("skills")

    # rag_scan_status is shared (0012_rag) — never dropped here.
    op.execute("DROP TYPE skill_source")
    op.execute("DROP TYPE skill_scope")
