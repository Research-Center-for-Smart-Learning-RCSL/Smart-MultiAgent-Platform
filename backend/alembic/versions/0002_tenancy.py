"""Tenancy schema — orgs, org_members, projects, project_members.

Implements §8 lifecycle + R5.02 Original-Creator invariant via an EXCLUDE
constraint (one OC per org) and the polymorphic owner CHECK on `projects`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0002_tenancy"
down_revision: str | Sequence[str] | None = "0001_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # EXCLUDE constraint needs btree_gist for the equality operator.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "orgs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.Text(), nullable=False),  # citext below
        sa.Column("creator_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.execute("ALTER TABLE orgs ALTER COLUMN name TYPE CITEXT USING name::citext")
    op.execute(
        "CREATE UNIQUE INDEX uq_orgs_name_active ON orgs (name) "
        "WHERE deleted_at IS NULL"
    )

    op.create_table(
        "org_members",
        sa.Column("org_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_original_creator", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("org_id", "user_id", name="pk_org_members"),
        sa.CheckConstraint("role IN ('owner','member')", name="org_members_role_valid"),
    )
    # R5.02: exactly one Original Creator per org, enforced at DB level.
    op.execute(
        "ALTER TABLE org_members ADD CONSTRAINT ex_org_members_one_oc "
        "EXCLUDE USING gist (org_id WITH =) WHERE (is_original_creator)"
    )

    op.create_table(
        "projects",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("owner_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("owner_org_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "(owner_user_id IS NOT NULL)::int + (owner_org_id IS NOT NULL)::int = 1",
            name="projects_owner_xor",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_projects_user_name ON projects (owner_user_id, name) "
        "WHERE deleted_at IS NULL AND owner_user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_projects_org_name ON projects (owner_org_id, name) "
        "WHERE deleted_at IS NULL AND owner_org_id IS NOT NULL"
    )

    op.create_table(
        "project_members",
        sa.Column("project_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("project_id", "user_id", name="pk_project_members"),
        sa.CheckConstraint(
            "role IN ('owner','member')",
            name="project_members_role_valid",
        ),
    )

    # Generic `version` bump trigger so PATCH callers can use If-Match.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION smap_bump_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.version IS NOT DISTINCT FROM OLD.version THEN
            NEW.version := OLD.version + 1;
          END IF;
          RETURN NEW;
        END
        $$;
        """
    )
    for tbl in ("users", "orgs", "projects"):
        op.execute(
            f"CREATE TRIGGER trg_{tbl}_bump_version "
            f"BEFORE UPDATE ON {tbl} FOR EACH ROW "
            f"EXECUTE FUNCTION smap_bump_version();"
        )


def downgrade() -> None:
    for tbl in ("users", "orgs", "projects"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_bump_version ON {tbl};")
    op.execute("DROP FUNCTION IF EXISTS smap_bump_version();")
    op.drop_table("project_members")
    op.execute("DROP INDEX IF EXISTS uq_projects_org_name")
    op.execute("DROP INDEX IF EXISTS uq_projects_user_name")
    op.drop_table("projects")
    op.execute("ALTER TABLE org_members DROP CONSTRAINT IF EXISTS ex_org_members_one_oc")
    op.drop_table("org_members")
    op.execute("DROP INDEX IF EXISTS uq_orgs_name_active")
    op.drop_table("orgs")
