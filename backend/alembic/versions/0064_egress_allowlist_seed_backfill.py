"""Backfill mcp_egress_allowlist for existing active search keys (R12.16).

Data-only, no schema change. Migration 0014 created ``mcp_egress_allowlist``
with no writer; the service-side fix (SearchKeyService.activate) only seeds
new activations going forward. Deployments with search keys activated
*before* that fix shipped still have an empty allowlist, so this backfill
grants each such project the one hostname its already-active provider needs
-- the same grant activate() would have made had the fix always existed.

Insert-only, ON CONFLICT DO NOTHING against
``uq_mcp_egress_allowlist_project_hostname`` (0014): a project that already
has the row (backfilled twice, or the operator added it manually) is left
untouched. ``added_by_user_id`` stays NULL (no human actor performed this
write) and ``note`` carries a fixed marker so downgrade can identify exactly
the rows this migration created, and only those.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064_egress_allowlist_seed_backfill"
down_revision: str | Sequence[str] | None = "0063_auth_identities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors contexts.keys.domain.search.SEARCH_PROVIDER_HOSTS. Inlined, not
# imported, so this migration keeps replaying correctly even if that module
# moves or gains a fifth provider later (same rationale as 0052's _DIMS).
_PROVIDER_HOSTS: dict[str, str] = {
    "brave": "api.search.brave.com",
    "serper": "google.serper.dev",
    "tavily": "api.tavily.com",
    "google_cse": "www.googleapis.com",
}

_NOTE = f"backfilled by migration {revision} (R12.16)"


def upgrade() -> None:
    bind = op.get_bind()
    insert = sa.text(
        "INSERT INTO mcp_egress_allowlist (project_id, hostname, added_by_user_id, note) "
        "SELECT project_id, :hostname, NULL, :note "
        "FROM search_keys "
        "WHERE provider = :provider AND is_active AND deleted_at IS NULL "
        "ON CONFLICT (project_id, hostname) DO NOTHING"
    )
    for provider, hostname in _PROVIDER_HOSTS.items():
        bind.execute(insert, {"provider": provider, "hostname": hostname, "note": _NOTE})


def downgrade() -> None:
    # Scoped to this migration's own marker AND a null actor, so operator- or
    # activation-added rows for the same hosts (added since, with a different
    # note and a real actor) survive untouched.
    stmt = sa.text("DELETE FROM mcp_egress_allowlist WHERE note = :note AND added_by_user_id IS NULL")
    op.execute(stmt.bindparams(note=_NOTE))
