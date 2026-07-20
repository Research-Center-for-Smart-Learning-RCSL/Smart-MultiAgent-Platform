"""Backfill deleted_at onto graph configs of already-deleted projects (task dossier:
docs/tasks/2026-07-17-knowmap-revision-divergence-sweep/, FU-2).

``ProjectService.soft_delete`` cascaded to skills only, so deleting a project left its
``knowmap_configs`` and ``graphrag_configs`` rows with ``deleted_at IS NULL``. Every
consumer that checked only the config's own ``deleted_at`` therefore kept treating them
as live. That was survivable while every build trigger sat behind a membership check a
deleted project already fails, and stopped being survivable once a background sweep began
enqueueing builds with no request behind it.

The service now cascades forward. This closes the gap for projects deleted before it did.

Each row is stamped with its *project's* ``deleted_at``, not ``now()``. That is the key
``restore_for_project`` matches on, so a project restored after this migration takes back
exactly the configs its own deletion accounted for, and leaves configs the user had
deleted separately alone.

The downgrade reverses only what this migration can prove it did: rows whose
``deleted_at`` still equals their project's. A config deleted on its own beforehand has a
different timestamp and is untouched in both directions.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060_cascade_deleted_projects_to_graph_configs"
down_revision: str | Sequence[str] | None = "0059_build_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

_TABLES: tuple[str, ...] = ("graphrag_configs", "knowmap_configs")


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        result = bind.execute(
            sa.text(
                f"UPDATE {table} AS c "  # noqa: S608 - fixed table list
                "SET deleted_at = p.deleted_at "
                "FROM projects AS p "
                "WHERE c.project_id = p.id "
                "AND p.deleted_at IS NOT NULL "
                "AND c.deleted_at IS NULL"
            )
        )
        if result.rowcount:
            _log.warning(
                "0060: soft-deleted %d %s row(s) belonging to already-deleted projects; "
                "these were live to any consumer checking only the config's own deleted_at",
                result.rowcount,
                table,
            )


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        # Only rows this migration could have stamped: deleted_at still identical to
        # the project's. Anything else was deleted on its own and must stay deleted.
        result = bind.execute(
            sa.text(
                f"UPDATE {table} AS c "  # noqa: S608 - fixed table list
                "SET deleted_at = NULL "
                "FROM projects AS p "
                "WHERE c.project_id = p.id "
                "AND p.deleted_at IS NOT NULL "
                "AND c.deleted_at = p.deleted_at"
            )
        )
        if result.rowcount:
            _log.warning("0060 downgrade: restored %d %s row(s)", result.rowcount, table)
