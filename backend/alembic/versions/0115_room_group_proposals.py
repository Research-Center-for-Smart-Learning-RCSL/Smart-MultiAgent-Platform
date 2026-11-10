"""Let guests propose and vote in a room group.

Spec: docs/tasks/2026-11-10-crat-lab-trial-fixes/spec.md, milestone 3
([R30.39], [R30.41], [R30.49]).

An anonymous guest's principal id is a ``guest_sessions.id``, never a
``users.id`` ([R5.04]), so the two ``users`` foreign keys left on the proposal
tables after 0098 refused every guest proposal and vote. They are dropped, as
0098 did for sessions and submissions, and each column gains the stored person
kind that 0098 introduced (``user`` or ``guest``), so a guest stays
distinguishable after its guest session is purged. User erasure no longer rides
the dropped CASCADE; ``purge_user_rows`` does it explicitly.

``group_kind`` says whether a group proposal or group session belongs to a
project Member Group or to the room group, whose id is the chatroom id. It is
meaningful only on group rows; personal sessions keep the default.

FORWARD COMPATIBLE. Every new column has a default that describes what old code
writes, and old code writes no guest row, so it runs on this schema unchanged.

DOWNGRADE restores the foreign keys only when no guest proposal or vote exists;
otherwise it refuses, since restoring them would fail on those rows anyway.
Delete guest proposals (``proposer_kind = 'guest'``) and guest votes
(``voter_kind = 'guest'``) first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0115_room_group_proposals"
down_revision: str = "0114_activation_round_brief"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERSON_KIND_SQL = "IN ('user', 'guest')"
GROUP_KIND_SQL = "IN ('member_group', 'room')"

_USER_FKS = (
    (
        "activity_group_proposals",
        "fk_activity_group_proposals_proposer_user_id_users",
        "proposer_user_id",
    ),
    (
        "activity_group_proposal_votes",
        "fk_activity_group_proposal_votes_user_id_users",
        "user_id",
    ),
)
# (table, column, default, allowed values, check name)
_KIND_COLUMNS = (
    (
        "activity_group_proposals",
        "proposer_kind",
        "user",
        PERSON_KIND_SQL,
        "ck_activity_group_proposals_proposer_kind",
    ),
    (
        "activity_group_proposal_votes",
        "voter_kind",
        "user",
        PERSON_KIND_SQL,
        "ck_activity_group_proposal_votes_voter_kind",
    ),
    (
        "activity_group_proposals",
        "group_kind",
        "member_group",
        GROUP_KIND_SQL,
        "ck_activity_group_proposals_group_kind",
    ),
    (
        "activity_sessions",
        "group_kind",
        "member_group",
        GROUP_KIND_SQL,
        "ck_activity_sessions_group_kind",
    ),
)


def upgrade() -> None:
    for table, column, default, allowed, check in _KIND_COLUMNS:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} text NOT NULL DEFAULT '{default}'")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {check}")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {check} CHECK ({column} {allowed})")
    for table, name, _column in _USER_FKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def downgrade() -> None:
    conn = op.get_bind()
    guests = conn.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM activity_group_proposals WHERE proposer_kind = 'guest')"
            " + (SELECT count(*) FROM activity_group_proposal_votes WHERE voter_kind = 'guest')"
        )
    ).scalar_one()
    rooms = conn.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM activity_group_proposals WHERE group_kind = 'room')"
            " + (SELECT count(*) FROM activity_sessions WHERE group_kind = 'room')"
        )
    ).scalar_one()
    if guests or rooms:
        raise RuntimeError(
            "0115 downgrade refused: guest proposals or votes, or room-group rows, exist; "
            "delete them first (see this migration's docstring)"
        )
    for table, name, column in _USER_FKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {name} "
            f"FOREIGN KEY ({column}) REFERENCES users (id) ON DELETE CASCADE"
        )
    for table, column, _default, _allowed, check in reversed(_KIND_COLUMNS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {check}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
