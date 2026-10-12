"""Per-member read filter on agent_group Concept Maps.

``agent_group_members.read_member_ids`` restricts what one member reads from its
group's Concept Map to relations whose provenance names one of the listed
members ([R11.22]). ``NULL`` means unrestricted, the behaviour before this
revision, so the column is nullable with no default and old code that never
reads it is unaffected. An empty array is a valid stored state: it is what a
member's list becomes when the last member it named is removed, and it reads
nothing until the Project Owner sets the list again.

The downgrade drops the column, and with it every restriction set since.

Dossier: ``docs/tasks/2026-10-12-graphrag-phase5b-member-filter``.

Revision ID: 0103_agent_group_member_read_filter
Revises: 0101_blank_display_names
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "0103_agent_group_member_read_filter"
down_revision: str = "0101_blank_display_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_group_members",
        sa.Column("read_member_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_group_members", "read_member_ids")
