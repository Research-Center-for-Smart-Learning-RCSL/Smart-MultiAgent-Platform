"""Observer presentation blocks: `agent_observations.blocks` ([R28.15]).

Revision ID: 0080_observation_presentation_blocks
Revises: 0079_member_groups

One additive JSONB column holding the ordered block array an observer agent
assembled through `present_observation`. `content_md` stays authoritative and is
written on the same insert as the blocks' markdown serialisation, so nothing that
reads an observation today (release to room, the release dialog's override, the
observer's own memory window) has to learn about blocks at all.

STORING THE SERIALISATION RATHER THAN DERIVING IT IS THE POINT. Downgrading drops
the column and loses the block structure, but every observation stays readable
because its markdown was persisted, not rendered on read. The same property is what
makes this forward-compatible in the other direction: pre-0080 code inserts valid
rows on the new schema (the server default supplies `[]`) and ignores the column on
read.

BOTH DIRECTIONS ARE A SINGLE TRANSACTION, with no autocommit block and no
CONCURRENTLY, for the reasons 0076 spells out at length;
``tests/unit/test_migration_autocommit_ordering.py`` pins the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0080_observation_presentation_blocks"
down_revision: str | Sequence[str] | None = "0079_member_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_observations",
        sa.Column("blocks", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    """Reverses cleanly and unconditionally.

    Purely additive on the way up: every pre-0080 observation reads as "no blocks",
    which is exactly what an empty array means. Dropping the column discards the
    block structure and nothing else — `content_md` already holds the serialisation.
    """
    op.drop_column("agent_observations", "blocks")
