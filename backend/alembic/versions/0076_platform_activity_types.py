"""Platform-scoped activity types and per-project opt-in ([R30.02], [R30.32], [R30.33]).

Revision ID: 0076_platform_activity_types
Revises: 0075_activity_policies

An `ActivityType` gains a `scope`. `project` is what every existing row already
is, so step 1's DEFAULT makes this a pure add with no backfill statement. A
`platform` row has no owning project, which is why `project_id` loses NOT NULL --
and why the two CHECKs below exist: `scope` and `project_id` must agree, so a
half-converted row (a platform type still pointing at a project, or a project
type with a NULL owner) cannot be written by the API or by an operator's UPDATE.

The second partial-unique is load-bearing rather than defensive.
`uq_activity_types_project_key_active` is `(project_id, key) WHERE deleted_at IS
NULL`, and PostgreSQL treats every NULL as distinct -- so the moment `project_id`
becomes nullable it stops constraining platform rows entirely, and two installs
could share a key with no error. `type_repo.create` string-matches index names to
raise `ActivityTypeKeyConflict`, so without this a platform conflict would surface
as a raw IntegrityError 500.

DOWNGRADE IS NOT UNCONDITIONAL. Restoring NOT NULL is impossible while platform
rows exist, and the alternative -- deleting rows an admin installed, along with
every session and submission that cascades from them -- is worse than a refused
downgrade. It therefore fails loudly and names the manual step.

BOTH DIRECTIONS ARE A SINGLE TRANSACTION, with no autocommit block and no
CONCURRENTLY. See the comment above the index build for why concurrency would buy
nothing here and cost retry-safety; ``tests/unit/test_migration_autocommit_ordering.py``
pins the rule for every migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0076_platform_activity_types"
down_revision: str | Sequence[str] | None = "0075_activity_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "activity_types",
        sa.Column("scope", sa.Text, nullable=False, server_default=sa.text("'project'")),
    )
    op.alter_column("activity_types", "project_id", existing_type=pg.UUID(as_uuid=True), nullable=True)

    op.create_check_constraint(
        "ck_activity_types_scope",
        "activity_types",
        "scope IN ('project', 'platform')",
    )
    # Written as an equivalence rather than two ORed implications so neither
    # direction can be forgotten: a project row must name its project, and a
    # platform row must not.
    op.create_check_constraint(
        "ck_activity_types_project_scope",
        "activity_types",
        "(scope = 'project') = (project_id IS NOT NULL)",
    )

    # Deliberately NOT CONCURRENTLY, unlike 0071/0072/0074.
    #
    # Those build indexes on high-write tables and take no stronger lock themselves,
    # so concurrency buys them something. This migration has already taken ACCESS
    # EXCLUSIVE on activity_types four times above (add_column, alter_column, and
    # both CHECKs) and holds it for its duration -- strictly stronger than the
    # ShareLock a plain CREATE INDEX takes, and it blocks readers too. Building
    # concurrently here would buy nothing and cost a great deal: CONCURRENTLY
    # cannot run inside a transaction, so it forces an autocommit_block, which
    # commits everything above it while the revision stamp still says 0075. A
    # failure after that point leaves the schema advanced, the version behind, and
    # the migration unretryable ([O4.04]).
    #
    # This rests on activity_types being a catalogue table -- one row per activity
    # type per project. If it ever grows to millions of rows, revisit: the lock is
    # then held for the length of a real index build.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_types_platform_key_active "
        "ON activity_types (key) "
        "WHERE project_id IS NULL AND deleted_at IS NULL"
    )

    op.create_table(
        "project_activity_type_optins",
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "activity_type_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # SET NULL, not CASCADE: deleting the admin who enabled a type must not
        # silently revoke a project's access to it mid-course.
        sa.Column(
            "enabled_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    # The reachability check reads by (project_id, activity_type_id) and the
    # listing reads every row for one project; the composite PK serves both. The
    # reverse direction -- "which projects opted into this type" -- drives the
    # admin delete cascade and has no PK prefix to use.
    op.create_index(
        "ix_project_activity_type_optins_type",
        "project_activity_type_optins",
        ["activity_type_id"],
    )


def assert_no_platform_types(bind: sa.engine.Connection) -> None:
    """Refuse the downgrade while platform-scoped rows exist.

    Restoring NOT NULL is impossible while they do, and the alternative --
    deleting rows an admin installed, cascading through ``activity_sessions`` and
    ``activity_submissions`` and taking authoritative research records with them
    ([R30.20]) -- is worse than a refused downgrade. A failed downgrade is
    recoverable; that is not.

    Module-level and taking its bind explicitly so the ``db``-tier test can
    exercise the refusal without executing a single DDL statement against the
    test database.
    """
    remaining = bind.execute(
        sa.text("SELECT count(*) FROM activity_types WHERE project_id IS NULL")
    ).scalar_one()
    if remaining:
        raise RuntimeError(
            f"{remaining} platform-scoped activity type(s) exist; 0076 cannot be reversed while "
            "activity_types.project_id must become NOT NULL again. Remove them first, e.g. "
            "DELETE FROM activity_types WHERE project_id IS NULL (this also deletes their "
            "sessions and submissions), then re-run the downgrade."
        )


def downgrade() -> None:
    # Before any DDL: a refusal must leave the schema exactly as it found it,
    # not half-reversed with the opt-in table already gone.
    assert_no_platform_types(op.get_bind())

    op.drop_index("ix_project_activity_type_optins_type", table_name="project_activity_type_optins")
    op.drop_table("project_activity_type_optins")

    # Not CONCURRENTLY, for the same reason as the upgrade: an autocommit block here
    # would commit the two drops above it while the stamp still says 0076, so a
    # failure in the constraint drops below would leave the opt-in table gone and
    # the migration unretryable.
    op.execute("DROP INDEX IF EXISTS uq_activity_types_platform_key_active")

    op.drop_constraint("ck_activity_types_project_scope", "activity_types", type_="check")
    op.drop_constraint("ck_activity_types_scope", "activity_types", type_="check")
    op.alter_column("activity_types", "project_id", existing_type=pg.UUID(as_uuid=True), nullable=False)
    op.drop_column("activity_types", "scope")
