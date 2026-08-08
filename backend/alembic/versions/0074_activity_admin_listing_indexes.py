"""Back the admin cross-project activity listings with real indexes.

Revision ID: 0074_activity_admin_listing_indexes
Revises: 0073_workflow_capability_backfill

``ActivityTypeRepository.list_all`` and ``ActivationRepository.list_all_active``
([R30.31]) are the only tenant-unscoped reads in the activities context. Both order
by ``(created_at DESC, id DESC)`` and paginate by keyset, and both justify keyset
over offset on the grounds that these tables grow with the whole platform -- but
without a matching index every page still sorts the entire filtered set, so that
justification does not hold. ``AdminActivitiesView`` requests 200 rows on each
mount, which would be a full scan and sort of every activity type on the platform
per page load.

The existing indexes (``ix_activity_types_project``, ``ix_activity_activations_chatroom``
from 0049/0050) are scoped lookups and cannot serve an unscoped ordered scan.

Both are partial, matching each query's own filter: live types, and ACTIVE
activations. That keeps them far smaller than the tables and lets the planner use
them for exactly the listing they exist for.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0074_activity_admin_listing_indexes"
down_revision: str | Sequence[str] | None = "0073_workflow_capability_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CONCURRENTLY in an autocommit block, following 0071: a plain CREATE INDEX
    # holds a SHARE lock for the whole build, and `activity_submissions` traffic
    # writes to these tables' neighbours during a live session. IF NOT EXISTS makes
    # a retry safe -- a failed concurrent build leaves an INVALID index occupying
    # the name, which must be dropped by hand before re-running.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activity_types_admin_listing "
            "ON activity_types (created_at DESC, id DESC) "
            "WHERE deleted_at IS NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_activity_activations_admin_listing "
            "ON activity_activations (created_at DESC, id DESC) "
            "WHERE status = 'active'"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_activity_activations_admin_listing")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_activity_types_admin_listing")
