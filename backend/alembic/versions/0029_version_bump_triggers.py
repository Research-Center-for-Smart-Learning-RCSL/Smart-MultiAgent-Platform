"""Add smap_bump_version triggers to agents + workflows — DB-5.

Revision ID: 0029_version_bump_triggers
Revises: 0028_notification_dedup_key

Closes the optimistic-locking split flagged by the 2026-05-21 systemic
audit (DB-5). Before this migration the `version` column was bumped two
different ways:

  * via the generic `smap_bump_version()` BEFORE-UPDATE trigger on
    `users/orgs/projects` (0002), `chatrooms` (0016) and `messages`
    (0017); and
  * by hand — `version = version + 1` written inside every repository
    method — on the trigger-less `agents` and `workflows` tables.

Two mechanisms for one column meant a future repository method on the
trigger-less tables could silently forget to bump `version`, breaking
`If-Match` concurrency control with no test or constraint to catch it.

After this migration the trigger is the *single* mechanism for every
versioned table. The now-redundant manual increments are removed from
the agents/workflows/chatrooms/messages repositories in the same change.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029_version_bump_triggers"
down_revision: str | Sequence[str] | None = "0028_notification_dedup_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Versioned tables that lacked the trigger and were app-bumped instead.
_TRIGGER_TABLES: tuple[str, ...] = ("agents", "workflows")


def upgrade() -> None:
    # Re-use the generic version-bump function from 0002_tenancy. It is
    # guarded (`IF NEW.version IS NOT DISTINCT FROM OLD.version`), so it is a
    # no-op for any caller that still sets `version` explicitly — the cutover
    # is therefore safe even mid-deploy while old app code is still running.
    for tbl in _TRIGGER_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{tbl}_bump_version "
            f"BEFORE UPDATE ON {tbl} FOR EACH ROW "
            f"EXECUTE FUNCTION smap_bump_version();"
        )


def downgrade() -> None:
    for tbl in _TRIGGER_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{tbl}_bump_version ON {tbl};")
