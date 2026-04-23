"""Baseline (empty) revision.

The authoritative DDL per §21 lands in Phase C (identity/tenancy), then each
subsequent phase adds its own revision. Bootstrap marks this revision as
current after creating the pgvector + pg_cron extensions (§25) so that the
first real migration in Phase C has a clean `alembic_version` row to advance.

Intentionally empty. Do **not** add DDL here — extensions are provisioned via
Postgres init SQL (docker-entrypoint-initdb.d) + `smap.bootstrap db-init`, not
Alembic, because extension creation requires superuser privileges that we
deliberately keep out of the app role.
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0000_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
