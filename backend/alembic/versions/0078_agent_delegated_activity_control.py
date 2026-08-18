"""Delegated activity control: a room-scoped grant on a bound agent ([R30.37]).

Revision ID: 0078_agent_delegated_activity_control
Revises: 0077_activity_session_activation

Two additions, one transaction.

`chatroom_agents` gains the grant itself. It lives on the binding rather than in a
companion table because the authority IS a property of that binding: unbinding the
agent removes the row and the grant with it, and binding the same agent elsewhere
confers nothing there. The shape admits three inconsistent states; two are closed
here by CHECK constraints and the third (an allowlist id pointing at a deleted or
unreachable type) is closed at read time, because a foreign key would not survive a
soft delete and would not answer the cross-project question either.

`ck_chatroom_agents_activity_grant` — a live grant must name at least one type. An
empty allowlist with the switch on is authority over nothing that still reads as
authority in every listing.

`ck_chatroom_agents_activity_grantor` — a live grant must name its grantor. This is
what makes `granted_by_user_id`'s `ON DELETE SET NULL` safe: deleting a granting
user does not silently produce an unattributable grant, it fails the CHECK loudly.
User deletion is already an admin operation, and a grant that cannot name the person
answerable for it must not run.

The reverse state — the switch off with an allowlist left behind — is deliberately
permitted. It preserves the teacher's selection across a revoke and re-grant; every
read path checks `may_control_activities` first, so the residue is a remembered
setting, never latent authority.

`activity_activations` gains `started_by_agent_id`, **with no foreign key**. [R30.05]
forbids the activities context from importing the agents context and
`tests/unit/test_activities_no_agents_import.py` enforces it statically; an FK would
put that same coupling in the schema. This matches how `activity_types.validator_config`
already holds an agent id and validates it at the route. A deleted agent leaves a
historical id that resolves to nothing, which is the correct reading of "the agent
that started this round no longer exists".

`started_by_user_id` stays NOT NULL and keeps recording the delegating teacher: the
facilitator's per-round progress event is addressed to `user_channel(started_by_user_id)`
and has no self-healing poll for that recipient, so an agent-valued or null column
would blind the teacher's panel.

FORWARD-COMPATIBLE. Every column is defaulted or nullable, so pre-0078 code inserts
valid rows on the new schema and ignores the new columns on read.

BOTH DIRECTIONS ARE A SINGLE TRANSACTION, with no autocommit block and no
CONCURRENTLY, for the reasons 0076 spells out at length;
``tests/unit/test_migration_autocommit_ordering.py`` pins the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0078_agent_delegated_activity_control"
down_revision: str | Sequence[str] | None = "0077_activity_session_activation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRANT_CHECK_NAME = "ck_chatroom_agents_activity_grant"
GRANTOR_CHECK_NAME = "ck_chatroom_agents_activity_grantor"

# `jsonb_array_length` is PostgreSQL-specific and only evaluable against a real
# server, which is why the constraint has a `db`-tier test rather than a unit one
# (backend/CLAUDE.md). Written out here so that test can quote the exact predicate.
GRANT_CHECK_SQL = "may_control_activities = false OR jsonb_array_length(activity_type_allowlist) > 0"
GRANTOR_CHECK_SQL = "may_control_activities = false OR granted_by_user_id IS NOT NULL"


def upgrade() -> None:
    op.add_column(
        "chatroom_agents",
        sa.Column(
            "may_control_activities",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "chatroom_agents",
        sa.Column(
            "activity_type_allowlist",
            pg.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "chatroom_agents",
        sa.Column(
            "granted_by_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_check_constraint(GRANT_CHECK_NAME, "chatroom_agents", sa.text(GRANT_CHECK_SQL))
    op.create_check_constraint(GRANTOR_CHECK_NAME, "chatroom_agents", sa.text(GRANTOR_CHECK_SQL))

    op.add_column(
        "activity_activations",
        sa.Column("started_by_agent_id", pg.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    """Reverses cleanly and unconditionally.

    Purely additive in both directions: no existing row's meaning changed on the way
    up (`may_control_activities` defaults to false, so every pre-0078 binding is
    ungranted), and nothing was transformed, so there is nothing to reconstruct on
    the way down. Dropping the columns discards the grants themselves, which is the
    intended meaning of downgrading past the feature that defines them.
    """
    op.drop_column("activity_activations", "started_by_agent_id")
    op.drop_constraint(GRANTOR_CHECK_NAME, "chatroom_agents", type_="check")
    op.drop_constraint(GRANT_CHECK_NAME, "chatroom_agents", type_="check")
    op.drop_column("chatroom_agents", "granted_by_user_id")
    op.drop_column("chatroom_agents", "activity_type_allowlist")
    op.drop_column("chatroom_agents", "may_control_activities")
