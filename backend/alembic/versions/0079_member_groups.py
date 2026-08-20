"""Member Groups: an optional per-project sub-grouping of people ([R13.28]-[R13.31]).

Revision ID: 0079_member_groups
Revises: 0078_agent_delegated_activity_control

Three tables and one column, one transaction.

`member_groups` is scoped to a project, not to an org: a group exists to narrow one
project's chat rooms below project level, and a group spanning projects would have
no room ACL to attach to. The name is unique per project, case-insensitively, and
only among live rows — a partial unique index on `lower(name)` rather than a plain
UNIQUE, so re-creating a group whose predecessor was soft-deleted is not a
collision. Soft delete plus the `smap_bump_version` trigger from 0002_tenancy gives
the same If-Match contract every other renameable resource here has.

`member_group_members` deliberately carries **no foreign key to `project_members`**.
[R13.28] says only current project members may be group members, and that rule is
enforced at the service, which can say why the request was refused. A composite FK
would enforce it in the schema at the cost of making project-membership removal fail
loudly instead of cascading — and the cascade is the behaviour the requirement
actually wants when someone leaves a project. `remove_user_from_projects` is where
that cleanup lives; a group membership outliving the project membership is a bug the
service prevents, not a state the schema permits and then trips over.

`chatroom_member_groups` lives in the conversation context's schema space even
though it names a tenancy row, exactly as `chatroom_guests` names a `users` row: a
binding of people to a room is a room ACL artifact. Cross-context foreign keys are
already the norm here (`workspaces.project_id`, `chatrooms.created_by_user_id`).

`chatrooms.allow_member_groups` defaults to **false**, which is what makes the whole
feature opt-in: a project that defines no group, and a room that enables no tier,
behave exactly as they did before this migration. A binding on a room whose flag is
off grants nothing ([R13.29]), so the two are independent and neither implies the
other.

NO CHECK CONSTRAINT PAIRS `allow_member_groups` WITH `allow_project_members`, and
the reason matters. [R13.04] makes them mutually exclusive and the API refuses the
combination with a 422, but a CHECK would also reject every pre-0079 row's implicit
state during a mixed-version deploy window, and would turn a future data fix into a
migration. The refusal belongs where it can explain itself.

FORWARD-COMPATIBLE. The new column is defaulted and the new tables are unread by
pre-0079 code, so the old image runs on the new schema.

BOTH DIRECTIONS ARE A SINGLE TRANSACTION, with no autocommit block and no
CONCURRENTLY, for the reasons 0076 spells out at length;
``tests/unit/test_migration_autocommit_ordering.py`` pins the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0079_member_groups"
down_revision: str | Sequence[str] | None = "0078_agent_delegated_activity_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "member_groups",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        # SET NULL, never RESTRICT: `AdminService.hard_delete_user` issues a raw
        # DELETE FROM users for GDPR erasure, and a RESTRICT here would abort it
        # naming a table the admin has no reason to connect to the request. See
        # 0078's docstring for the full account of that failure mode.
        sa.Column(
            "created_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_member_groups_project_name_live",
        "member_groups",
        ["project_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_member_groups_project",
        "member_groups",
        ["project_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.execute(
        "CREATE TRIGGER trg_member_groups_bump_version "
        "BEFORE UPDATE ON member_groups FOR EACH ROW "
        "EXECUTE FUNCTION smap_bump_version();"
    )

    op.create_table(
        "member_group_members",
        sa.Column(
            "member_group_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("member_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("member_group_id", "user_id", name="pk_member_group_members"),
    )
    # The room ACL asks "which of this user's groups are bound here", so the
    # user-first lookup is the hot one and the PK's leading column is the wrong
    # way round for it.
    op.create_index("ix_member_group_members_user", "member_group_members", ["user_id"])

    op.create_table(
        "chatroom_member_groups",
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_group_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("member_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "chatroom_id", "member_group_id", name="pk_chatroom_member_groups"
        ),
    )
    op.create_index(
        "ix_chatroom_member_groups_group", "chatroom_member_groups", ["member_group_id"]
    )

    op.add_column(
        "chatrooms",
        sa.Column(
            "allow_member_groups",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chatrooms", "allow_member_groups")
    op.drop_index("ix_chatroom_member_groups_group", table_name="chatroom_member_groups")
    op.drop_table("chatroom_member_groups")
    op.drop_index("ix_member_group_members_user", table_name="member_group_members")
    op.drop_table("member_group_members")
    op.execute("DROP TRIGGER IF EXISTS trg_member_groups_bump_version ON member_groups;")
    op.drop_index("ix_member_groups_project", table_name="member_groups")
    op.drop_index("uq_member_groups_project_name_live", table_name="member_groups")
    op.drop_table("member_groups")
