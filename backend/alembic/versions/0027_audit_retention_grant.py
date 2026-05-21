"""Grant audit-retention role membership so the retention worker can SET ROLE.

Revision ID: 0027_audit_retention_grant
Revises: 0026_impersonation_access_jti
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027_audit_retention_grant"
down_revision: str | Sequence[str] | None = "0026_impersonation_access_jti"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # DB-4: the nightly retention worker runs `SET ROLE smap_audit_retention`
    # to bypass the audit_logs append-only trigger (R17.01 / R17.04). Postgres
    # permits `SET ROLE` only to a role the session user is a *member* of, and
    # migration 0004 created the role without granting membership anywhere — so
    # `_purge_audit_logs` failed every night with "permission denied to set
    # role" and audit logs were never purged.
    #
    # `smap_audit_retention` is NOLOGIN NOINHERIT: granting membership lets the
    # app/worker role SET ROLE *deliberately* without ever passively inheriting
    # the trigger-bypass privilege. That is the intended mechanism.
    #
    # CURRENT_USER is the role running migrations. This assumes the API/worker
    # connect as that same role (the default single-DSN deployment). If your
    # deployment uses a separate migration role, also run, as an ops step:
    #     GRANT smap_audit_retention TO <runtime_role>;
    op.execute("GRANT smap_audit_retention TO CURRENT_USER")


def downgrade() -> None:
    op.execute("REVOKE smap_audit_retention FROM CURRENT_USER")
