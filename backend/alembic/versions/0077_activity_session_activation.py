"""Bind an activity session to the activation it was answered under ([R30.01], [R30.22]).

Revision ID: 0077_activity_session_activation
Revises: 0076_platform_activity_types

`activity_sessions` was keyed on (activity_type_id, chatroom_id, subject_user_id),
which says nothing about *which run* of that activity a submission belongs to. A
facilitator starting the same type twice in one room therefore had every
participant's second-round submissions land in the still-open first-round session,
continuing its attempt_no sequence -- two rounds merged into one history that no
client can separate afterwards.

`activation_id` fixes that. It is nullable only because pre-0077 rows have no
round to point at; every row written after this migration sets it, which is a
writer invariant rather than a schema one (see `session_repo.py`).

`completed_at` is the participant's own "I am finished" declaration, deliberately
NOT the `status` column: `status` answers "can this session still take
submissions" and is driven by the facilitator ending the round, so overloading it
would mark the whole class complete the moment the teacher stops the activity.

THE NEW UNIQUE IS PLAIN, NOT PARTIAL. One session per (activation, subject),
whatever its status -- so a participant who declares themselves done and then
answers again keeps one continuous attempt sequence, and a second round is
structurally a different row instead of depending on the first having been
closed. PostgreSQL treats NULLs as distinct in a unique index, so the legacy rows
left with a NULL `activation_id` are unconstrained by it, which is correct --
step 2 has already closed every one of them.

`uq_activity_sessions_open` IS DELIBERATELY LEFT IN PLACE. It is redundant under
the new design and the obvious move is to drop it here, but that would break the
forward-compatibility rule this repo holds migrations to (backend/CLAUDE.md: old
code runs on new schema). Pre-0077 `create_open` relies on that index for its
`ON CONFLICT DO NOTHING`; with the index gone it inserts `activation_id = NULL`,
NULLs are distinct under the new unique, and two concurrent first submissions in
the window between `alembic upgrade` and the app restart would produce two open
sessions for one subject -- the very split this migration exists to prevent.

Keeping it costs nothing, because the new design already satisfies it: ending a
round closes its sessions (`ActivationService.end`), so a subject never holds two
*open* sessions for one (type, room) even across rounds. Dropping it is the
contract half of an expand/contract pair and belongs in a later migration, once
this one's code is deployed -- recorded as FU-7 of
`docs/tasks/2026-08-17-activity-participant-lifecycle`.

BOTH DIRECTIONS ARE A SINGLE TRANSACTION, with no autocommit block and no
CONCURRENTLY, for the reasons 0076 spells out at length;
``tests/unit/test_migration_autocommit_ordering.py`` pins the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0077_activity_session_activation"
down_revision: str | Sequence[str] | None = "0076_platform_activity_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The two data statements are module-level so a ``db``-tier test can execute the
# exact SQL this migration runs, against a real PostgreSQL, without tearing down
# the schema every other test in that job is using (the shape 0076 uses for
# ``assert_no_platform_types``). A hand-copied duplicate in the test would prove
# nothing about what the migration does.

# Adopt the sessions a live round can still claim. At most one activation per
# room is `active` (uq_activity_activations_active), so the join cannot match two
# rows and the UPDATE is deterministic. A class under way when this runs keeps
# its attempt history.
BACKFILL_ACTIVATION_SQL = (
    "UPDATE activity_sessions s "
    "SET activation_id = a.id "
    "FROM activity_activations a "
    "WHERE s.status = 'open' "
    "AND a.status = 'active' "
    "AND a.chatroom_id = s.chatroom_id "
    "AND a.activity_type_id = s.activity_type_id"
)

# Close what nothing claims. These sessions cannot receive a submission today
# either ([R30.22] requires an active activation for the type), so this takes
# nothing away; leaving them open is what would let the NEXT round adopt them,
# which is the defect this migration exists to end. Not reversible by
# downgrade() -- see the dossier's risk section.
CLOSE_UNCLAIMED_SQL = (
    "UPDATE activity_sessions "
    "SET status = 'closed', closed_at = now() "
    "WHERE status = 'open' AND activation_id IS NULL"
)


def upgrade() -> None:
    op.add_column(
        "activity_sessions",
        sa.Column(
            "activation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("activity_activations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "activity_sessions",
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Order matters: adopt first, then close what is left unclaimed.
    op.execute(BACKFILL_ACTIVATION_SQL)
    op.execute(CLOSE_UNCLAIMED_SQL)

    # Serves the per-round count and close as well as the identity lookup: a
    # btree on (activation_id, subject_user_id) is usable for a WHERE on its
    # leading column, so no separate single-column index is warranted.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_sessions_activation_subject "
        "ON activity_sessions (activation_id, subject_user_id)"
    )


def downgrade() -> None:
    """Reverses cleanly and unconditionally.

    Nothing to restore and nothing to de-duplicate: `uq_activity_sessions_open`
    was never dropped (see the module docstring), and it is what has been keeping
    a subject to one open session per (type, room) throughout. The only
    irreversible part of this migration is upgrade step 2, which closed sessions
    no round claimed -- a `status` flip on rows that could not receive a
    submission either way. No submission row is touched in either direction.
    """
    op.execute("DROP INDEX IF EXISTS uq_activity_sessions_activation_subject")
    op.drop_column("activity_sessions", "completed_at")
    op.drop_column("activity_sessions", "activation_id")
