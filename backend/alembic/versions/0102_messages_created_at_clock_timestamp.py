"""Stamp ``messages.created_at`` at insert time, not at transaction start.

``now()`` is the start of the current transaction. An agent reply is inserted in
a transaction opened when its provider stream began, so it was back-dated below
every message committed during the stream, and clients' ``since`` cursor, which
orders by ``created_at``, had already passed it. ``clock_timestamp()`` is the
time of the insert statement itself. Rows already stored keep their stamps: the
true insert time was never recorded.

Numbered 0102 because 0100 and 0101 are claimed by approved, unbuilt dossiers;
it revises the head at build time.

Dossier: ``docs/tasks/2026-10-07-agent-reply-delivery-gaps``.

Revision ID: 0102_messages_created_at_clock_timestamp
Revises: 0099_guest_removal_and_bans
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0102_messages_created_at_clock_timestamp"
down_revision: str = "0099_guest_removal_and_bans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ALTER COLUMN created_at SET DEFAULT clock_timestamp()")


def downgrade() -> None:
    op.execute("ALTER TABLE messages ALTER COLUMN created_at SET DEFAULT now()")
