"""Support the retention sweep's F-17 and V-5 fixes with real indexes.

Revision ID: 0071_retention_sweep_indexes
Revises: 0070_knowledge_document_failure_code

``_sweep_orphaned_subagent_roots`` (F-17) now filters on
``(parent_id, run_context->>'synthetic_root')`` and orders by ``spawned_at``
before applying its ``LIMIT 500`` -- without an index, that sorts the whole
synthetic-root population every night rather than stopping at 500.

``RetentionService.purge_once`` (V-5/FU-3) now orders its victim select by
``(created_at, id)`` and recomputes ``oldest_kept_at`` with a post-delete
``min(created_at)`` -- both become full scans on ``messages`` without a plain
btree on ``created_at``. See spec §7.1/§7.3 Q-4 (user confirmed: add it).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0071_retention_sweep_indexes"
down_revision: str | Sequence[str] | None = "0070_knowledge_document_failure_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CONCURRENTLY, in an autocommit block: a plain CREATE INDEX takes a SHARE
    # lock for the whole build, which on `messages` -- the highest-write table in
    # the system -- is a write outage for the length of the migration. The
    # concurrent build cannot run inside a transaction, which is what
    # autocommit_block provides.
    #
    # IF NOT EXISTS makes a retry safe: a failed concurrent build leaves an
    # INVALID index behind rather than nothing, and re-running would otherwise
    # fail on the duplicate name. An INVALID index left by a failed build must be
    # dropped by hand before retrying -- it is not used by the planner but it does
    # occupy the name.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_agent_instances_synthetic_root "
            "ON agent_instances (spawned_at) "
            "WHERE parent_id IS NULL AND run_context->>'synthetic_root' = 'true'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_messages_created_at ON messages (created_at)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_messages_created_at")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_agent_instances_synthetic_root")
