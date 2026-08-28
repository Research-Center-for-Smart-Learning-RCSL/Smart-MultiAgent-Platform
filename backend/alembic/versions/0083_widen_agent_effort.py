"""Widen `agent_effort` to the cross-provider value union (R9.03a, Q-3).

Revision ID: 0083_widen_agent_effort
Revises: 0082_live_draft_grant_and_disclosure

`agent_effort` (0039) held only `low`/`medium`/`high` — the common subset every
provider accepted at the time. The per-model capability table (R9.03a) needs to
express a wider union (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`,
`max`); which subset a given model actually accepts is a capability-table
field, not an enum concern, so the enum widens to the union once rather than
per model family that adds a value later.

`ALTER TYPE ... ADD VALUE` cannot run inside the same transaction as a query
that uses the new value (PostgreSQL restriction), but adding the value itself
is fine inside alembic's own migration transaction. This migration itself
never queries the new values, so it is safe standalone -- but the restriction
still applies to whatever comes after it: a later migration that queries
`agent_effort`'s four new values (`none`/`minimal`/`xhigh`/`max`) must not run
in the SAME `alembic upgrade head` invocation as this one (e.g. a fresh
environment migrating straight through from before 0082), since alembic's
default `upgrade head` runs the whole chain in one transaction and the two
migrations would then share it.

Downgrade is lossy by construction: PostgreSQL cannot drop an enum value in
place, so shrinking back to the original three means recreating the type, which
fails if any row holds one of the four new values. The downgrade therefore
nulls those rows first rather than raising — a documented data loss, not a
silently failing migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0083_widen_agent_effort"
down_revision: str = "0082_live_draft_grant_and_disclosure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_VALUES = ("none", "minimal", "xhigh", "max")
_ORIGINAL_VALUES = ("low", "medium", "high")


def upgrade() -> None:
    for value in _NEW_VALUES:
        op.execute(f"ALTER TYPE agent_effort ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Any row holding a value the original three-member enum cannot represent
    # is nulled rather than blocking the downgrade — recorded here rather than
    # discovered as a failed migration.
    original_values_sql = ", ".join(f"'{v}'" for v in _ORIGINAL_VALUES)
    op.execute(f"UPDATE agents SET effort = NULL WHERE effort::text NOT IN ({original_values_sql})")
    op.execute("ALTER TYPE agent_effort RENAME TO agent_effort_widened")
    original = sa.Enum(*_ORIGINAL_VALUES, name="agent_effort")
    original.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE agents ALTER COLUMN effort TYPE agent_effort "
        "USING effort::text::agent_effort"
    )
    op.execute("DROP TYPE agent_effort_widened")
