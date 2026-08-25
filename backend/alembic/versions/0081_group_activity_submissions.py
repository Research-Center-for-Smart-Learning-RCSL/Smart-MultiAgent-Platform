"""A Member Group may be the subject of an activity session ([R30.39]-[R30.41]).

Revision ID: 0081_group_activity_submissions
Revises: 0080_observation_presentation_blocks

Three changes, one purpose: let a project Member Group ([R13.28]) own an
`ActivitySession` the way a user does today, reached only through a proposal its
members vote on.

RELAXING `subject_user_id` IS THE RISKY PART AND THE CHECK IS ITS REPLACEMENT.
Before this migration the column's NOT NULL was the only thing asserting that a
session has a subject at all, and several readers rely on that implicitly. The
CHECK `ck_activity_sessions_one_subject` restates it as the wider invariant --
exactly one of the two subject columns is set, never both and never neither -- so
nothing is weakened, only generalised. It lands in THIS migration, not a later
one: a window in which a session may legally have no subject is a window in which
one gets written.

WHY NO FOREIGN KEY ON `subject_member_group_id` OR ON `member_group_id`. The
groups table belongs to the tenancy context and [R30.09] forbids the cross-context
join a constraint would invite. A deleted group leaves an id that resolves to
nothing, which is the correct reading of "the group that answered this is gone" --
the same shape `activity_activations.started_by_agent_id` uses for agents (0078).

THE GROUP UNIQUE IS PLAIN, NOT PARTIAL, mirroring 0077's reasoning: PostgreSQL
treats NULLs as distinct in a unique index, so `(activation_id,
subject_member_group_id)` constrains group sessions and leaves every personal row
(NULL group) unconstrained by it, while 0077's `(activation_id,
subject_user_id)` does the reverse. Neither index can see the other's population,
and each closes its own two-concurrent-first-opens race.

`uq_activity_sessions_open` (0049) is likewise unaffected: it keys on
`subject_user_id`, which a group session leaves NULL, so a group's open session
never contends with a member's own open session for the same (type, room). That
is the intent -- [R30.39] makes the two separate.

`activity_group_proposals.status` IS TEXT + CHECK, NOT A PG ENUM, matching
`activity_types.scope` (0076) and `activity_policies.scope` (0075): adding a
terminal status stays a code change rather than a migration against a type other
tables reference.

FORWARD COMPATIBLE. Old code writes `subject_user_id` and satisfies the CHECK,
never sets `group_config` (NULL means individual-only, which is every type that
exists today), and never reads the two new tables. DOWNGRADE IS NOT LOSSLESS and
says so explicitly rather than failing halfway: a group session cannot exist under
the restored NOT NULL, so `downgrade()` deletes those sessions -- and their
submissions with them, through the existing ON DELETE CASCADE.

BOTH DIRECTIONS ARE A SINGLE TRANSACTION, with no autocommit block and no
CONCURRENTLY, for the reasons 0076 spells out at length;
``tests/unit/test_migration_autocommit_ordering.py`` pins the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0081_group_activity_submissions"
down_revision: str | Sequence[str] | None = "0080_observation_presentation_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Module-level so a ``db``-tier test can execute the exact predicate this
# migration installs, rather than a hand-copied duplicate that proves nothing
# about the migration (the shape 0076 and 0077 use).
ONE_SUBJECT_CHECK_SQL = "(subject_user_id IS NULL) <> (subject_member_group_id IS NULL)"

# The downgrade's data statement, for the same reason. Deleting the session is
# what deletes its submissions: `activity_submissions.session_id` is ON DELETE
# CASCADE (0049), so this line is the whole irreversibility.
DROP_GROUP_SESSIONS_SQL = "DELETE FROM activity_sessions WHERE subject_member_group_id IS NOT NULL"


def upgrade() -> None:
    op.add_column(
        "activity_types",
        sa.Column("group_config", pg.JSONB, nullable=True),
    )

    op.add_column(
        "activity_sessions",
        sa.Column("subject_member_group_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column("activity_sessions", "subject_user_id", nullable=True)
    op.create_check_constraint(
        "ck_activity_sessions_one_subject",
        "activity_sessions",
        sa.text(ONE_SUBJECT_CHECK_SQL),
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_sessions_activation_group "
        "ON activity_sessions (activation_id, subject_member_group_id)"
    )

    op.create_table(
        "activity_group_proposals",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "chatroom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chatrooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_activations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activity_type_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_types.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # No FK: see the module docstring.
        sa.Column("member_group_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "proposer_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # The voter set pinned at creation ([R30.41]). Stored rather than re-read
        # at resolution: a membership change mid-vote must move neither the
        # ballot nor the bar.
        sa.Column("voter_user_ids", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("required_approvals", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # SET NULL, not CASCADE: a purged submission must not erase the record
        # that a group agreed to something. The proposal keeps its `accepted`
        # status and loses only the pointer.
        sa.Column(
            "submission_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_submissions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('open', 'accepted', 'rejected', 'withdrawn', 'expired')",
            name="ck_activity_group_proposals_status",
        ),
        sa.CheckConstraint(
            "required_approvals >= 1",
            name="ck_activity_group_proposals_required_approvals",
        ),
    )
    # Vote-splitting is prevented HERE, not in the application: two competing
    # open proposals would divide a group's votes and neither could pass, and a
    # concurrent double-propose must fail at the database rather than depend on
    # a read-then-write the two racers both pass.
    op.create_index(
        "uq_activity_group_proposals_open",
        "activity_group_proposals",
        ["activation_id", "member_group_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    # The worker's expiry sweep scans open proposals by deadline; every other
    # row is dead weight to it.
    op.create_index(
        "ix_activity_group_proposals_expiry",
        "activity_group_proposals",
        ["expires_at"],
        postgresql_where=sa.text("status = 'open'"),
    )
    # The participant's read is "what is my group deciding in this room".
    op.create_index(
        "ix_activity_group_proposals_room",
        "activity_group_proposals",
        ["chatroom_id", "member_group_id"],
    )

    op.create_table(
        "activity_group_proposal_votes",
        sa.Column(
            "proposal_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_group_proposals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("choice", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("proposal_id", "user_id", name="pk_activity_group_proposal_votes"),
        sa.CheckConstraint(
            "choice IN ('approve', 'reject')",
            name="ck_activity_group_proposal_votes_choice",
        ),
    )


def downgrade() -> None:
    """Reverses, but NOT losslessly, and deliberately says which part is lost.

    A group session cannot exist under the restored NOT NULL, so every one is
    deleted here -- taking its submissions with it through
    `activity_submissions.session_id`'s ON DELETE CASCADE (0049). Doing it
    explicitly is the point: the alternative is an `alter_column` that aborts
    against the first group row and leaves the schema half-reverted.

    Personal sessions and their submissions are untouched in both directions.
    """
    op.drop_table("activity_group_proposal_votes")
    op.drop_index("ix_activity_group_proposals_room", table_name="activity_group_proposals")
    op.drop_index("ix_activity_group_proposals_expiry", table_name="activity_group_proposals")
    op.drop_index("uq_activity_group_proposals_open", table_name="activity_group_proposals")
    op.drop_table("activity_group_proposals")

    op.execute("DROP INDEX IF EXISTS uq_activity_sessions_activation_group")
    op.drop_constraint("ck_activity_sessions_one_subject", "activity_sessions", type_="check")
    op.execute(DROP_GROUP_SESSIONS_SQL)
    op.alter_column("activity_sessions", "subject_user_id", nullable=False)
    op.drop_column("activity_sessions", "subject_member_group_id")

    op.drop_column("activity_types", "group_config")
