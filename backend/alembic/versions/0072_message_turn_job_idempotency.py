"""Make an agent reply idempotent against a replayed turn job.

Revision ID: 0072_message_turn_job_idempotency
Revises: 0071_retention_sweep_indexes

A turn commits its reply with post-commit work still to run, and its lock is
released by the cancellation unwind, so a re-run could re-assemble a history
that already contained the reply and post a second one. `wakeup_agent` is now
`max_tries=1`, which closes the arq half; this index is the durable half, for
the case that guard cannot see -- above all a turn that lost its lock and ran
beside another one.

Index-only: `messages.metadata` is already JSONB, so the key rides in it and no
column is added. The predicate excludes every row without the key, so existing
messages -- and every non-agent message, which never carries one -- are
unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0072_message_turn_job_idempotency"
down_revision: str | Sequence[str] | None = "0071_retention_sweep_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CONCURRENTLY in an autocommit block, for the same reason as 0071: a plain
    # CREATE INDEX takes a SHARE lock for the whole build, and on `messages` --
    # the highest-write table in the system -- that is a write outage for the
    # length of the migration.
    #
    # IF NOT EXISTS makes a retry safe. A failed concurrent build leaves an
    # INVALID index behind rather than nothing; for a UNIQUE index that is worth
    # saying twice, because an invalid unique index enforces nothing. Drop it by
    # hand before retrying.
    #
    # `IS NOT NULL` rather than the `?` containment operator: it is equivalent
    # here (a JSON null is not a job id either) and avoids a bare `?` in a
    # driver that reads it as a bind placeholder.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_messages_turn_job_id "
            "ON messages ((metadata->>'turn_job_id')) "
            "WHERE metadata->>'turn_job_id' IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ux_messages_turn_job_id")
