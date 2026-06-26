"""Grant SELECT/DELETE on audit_logs to the retention role (DB-4 follow-up).

0004 created `smap_audit_retention` plus the append-only trigger that lets that
role bypass the UPDATE/DELETE guard, and 0027 granted the app/worker role
membership so it can `SET ROLE smap_audit_retention`. But the role itself was
never granted the underlying *table* privileges, so Postgres rejected the
nightly purge on the privilege check — before the trigger could run — with
"permission denied for table audit_logs". The purge reads ids in a subquery and
then deletes, so the role needs both SELECT and DELETE. GRANT is idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032_audit_retention_delete_grant"
down_revision: str | Sequence[str] | None = "0031_guest_display_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("GRANT SELECT, DELETE ON audit_logs TO smap_audit_retention")


def downgrade() -> None:
    op.execute("REVOKE SELECT, DELETE ON audit_logs FROM smap_audit_retention")
