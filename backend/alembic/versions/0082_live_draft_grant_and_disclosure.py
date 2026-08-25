"""Live draft reading: a per-binding grant and a per-room disclosure flag (§32).

Revision ID: 0082_live_draft_grant_and_disclosure
Revises: 0081_group_activity_submissions

Two boolean columns, one transaction. Neither holds a draft: a draft never reaches
PostgreSQL at all ([R32.02]). What lands here is only the *authority* to read one and
the *disclosure* of that authority, both of which have to survive a restart and a
worker that has never seen the room.

`chatroom_agents.may_read_drafts` sits on the binding for the reason
`may_control_activities` does (0078): the authority IS a property of that binding, so
unbinding the agent removes the row and the grant with it, and binding the same agent
into another room confers nothing there ([R32.03]).

**It reuses `granted_by_user_id` rather than adding a second grantor column**, and the
consequence is deliberate: revoking activity control does not revoke draft reading,
but *unbinding* clears both, and a room whose grantor has been erased has neither. The
alternative — a `drafts_granted_by_user_id` beside it — would double a column whose
only consumers are two fail-closed reads that ask the same question ("is there an
answerable person behind this authority?"). Two columns would also admit a state where
the two grants name different grantors, which no UI can produce and no reader could
interpret.

THERE IS DELIBERATELY NO CHECK CONSTRAINT HERE, and 0078's reasoning is the reason.
A `may_read_drafts => granted_by_user_id IS NOT NULL` check is the obvious partner to
`ck_chatroom_agents_activity_grant`, and it makes `ON DELETE SET NULL` unsafe in
exactly the way 0078 records: `AdminService.hard_delete_user` issues `DELETE FROM
users`, PostgreSQL performs the SET NULL, and the check aborts a GDPR erasure naming a
table the admin cannot connect to the request. The invariant is enforced at read
instead — `ChatroomAgentRepository.draft_read_grant` returns ``None`` when the grantor
is null, so a grant nobody is answerable for confers nothing and supplies no tool.

`chatrooms.disclose_drafts` defaults **true**, unlike every access flag on this table
and like `disclose_observers` (0069). A room that upgrades into this schema therefore
starts out telling its participants, and a creator has to act to stop it ([R32.05]).
The default is the safe direction here in a way it is not for an access flag: the
failure mode of disclosing without a reader is a chip nobody needed, and the failure
mode of the opposite is a person's unsent words being read with nothing on screen to
say so.

FORWARD-COMPATIBLE. Both columns carry server defaults, so pre-0082 code inserts valid
rows on the new schema and ignores the new columns on read. Pre-0082 code also never
sets `may_read_drafts`, so the schema alone grants nothing.

BOTH DIRECTIONS ARE A SINGLE TRANSACTION, with no autocommit block and no
CONCURRENTLY, for the reasons 0076 spells out at length;
``tests/unit/test_migration_autocommit_ordering.py`` pins the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0082_live_draft_grant_and_disclosure"
down_revision: str | Sequence[str] | None = "0081_group_activity_submissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chatroom_agents",
        sa.Column("may_read_drafts", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "chatrooms",
        sa.Column("disclose_drafts", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    """Reverses cleanly, and the direction it fails in is the safe one.

    Dropping `may_read_drafts` revokes every grant in every room at once, so the
    tool stops being offered on the next turn and the WS handler stores nothing
    further. Drafts already in Redis are not deleted here and do not need to be:
    they are TTL-bounded ([R32.02]) and nothing can read them once the grant column
    is gone.

    Dropping `disclose_drafts` loses each room's disclosure choice. Re-upgrading
    restores the default `true` rather than the previous value, which is the
    direction that over-discloses rather than under-discloses.
    """
    op.drop_column("chatrooms", "disclose_drafts")
    op.drop_column("chatroom_agents", "may_read_drafts")
