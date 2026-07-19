"""last_build_state: PG ENUM -> Text + CHECK, plus recovery_unavailable (task dossier:
docs/tasks/2026-07-17-graphrag-reset-expired-recovery/).

Adds a seventh build state, ``recovery_unavailable``: a config whose in-flight build can
be proven irrecoverable (the 24-hour Redis pointer/snapshot window lapsed) is neither
ordinary ``failed`` -- which is readable and rebuildable -- nor safely re-reconcilable.
See the dossier for why the state model, not just the reset path, had to change.

The state set is carried as ``sa.Text`` + CHECK rather than by extending the
``graphrag_build_state`` ENUM, per 0056_skills' rule: this backend has no
``ALTER TYPE ... ADD VALUE`` precedent, growing value sets use Text + CHECK, and the
shape copied is ``project_embedding_pins.kind`` (0052). PostgreSQL cannot remove an enum
value, so the additive route's downgrade would have had to rebuild the shared type and
remap both tables anyway -- exactly what this migration does once, forwards.

``graphrag_build_state`` was shared by ``graphrag_configs`` and ``knowmap_configs`` and by
nothing else, so both convert together and the type is dropped.

Forward-compatible: old code reading the column still gets the same strings, and no old
code writes the new value. The downgrade re-mints the type, but ``recovery_unavailable``
will not exist in it -- those rows are remapped to ``failed_compensating`` first, which is
the conservative direction (blocked for reads, and visible to the reconciler again).

The constraint name is spelled out in raw SQL on purpose. ``shared_kernel/db``'s
``ck_%(table_name)s_%(constraint_name)s`` convention is also applied to CHECK constraints
that are given an explicit name, so a name passed to ``sa.CheckConstraint`` gets prefixed
a second time (``project_embedding_pins`` carries exactly that mismatch today -- FU-6).
The ORM side therefore passes the short ``build_state_valid`` and lets the convention
produce the same literal used here.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0058_graphrag_build_state_text"
down_revision: str | Sequence[str] | None = "0057_context_token_cap_bound"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

_TABLES: tuple[str, ...] = ("graphrag_configs", "knowmap_configs")

# The six values minted by 0013 -- the exact membership of the dropped/re-minted ENUM.
_LEGACY_STATES: tuple[str, ...] = (
    "idle",
    "running",
    "neo4j_committed",
    "qdrant_committed",
    "failed_compensating",
    "failed",
)
_NEW_STATE = "recovery_unavailable"
_STATES: tuple[str, ...] = (*_LEGACY_STATES, _NEW_STATE)

# The state a downgrade parks an irrecoverable config in: still read-blocked, and back
# inside the reconciler's sweep set, so pre-0058 code treats it as stuck rather than
# healthy. Never `failed` -- that is readable.
_DOWNGRADE_STATE = "failed_compensating"


def _check_name(table: str) -> str:
    return f"ck_{table}_build_state_valid"


def _values_sql() -> str:
    return ", ".join(f"'{v}'" for v in _STATES)


def upgrade() -> None:
    for table in _TABLES:
        # Drop the default first: it is typed `'idle'::graphrag_build_state` and would
        # block both the column rewrite and the DROP TYPE below.
        op.execute(f"ALTER TABLE {table} ALTER COLUMN last_build_state DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN last_build_state " f"TYPE text USING last_build_state::text"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN last_build_state SET DEFAULT 'idle'")
        # The type rewrite above already took ACCESS EXCLUSIVE and rewrote every row, so
        # there is nothing for NOT VALID + VALIDATE (0057's pattern) to buy here.
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {_check_name(table)} "
            f"CHECK (last_build_state IN ({_values_sql()}))"
        )

    op.execute("DROP TYPE graphrag_build_state")


def downgrade() -> None:
    op.execute(
        "CREATE TYPE graphrag_build_state AS ENUM (" + ", ".join(f"'{v}'" for v in _LEGACY_STATES) + ")"
    )

    bind = op.get_bind()
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {_check_name(table)}")

        # `recovery_unavailable` has no representation in the re-minted type, so it must
        # go before the cast or the ALTER fails on those rows.
        result = bind.execute(
            sa.text(
                f"UPDATE {table} SET last_build_state = :fallback "  # noqa: S608 - fixed table list
                f"WHERE last_build_state = :unavailable"
            ),
            {"fallback": _DOWNGRADE_STATE, "unavailable": _NEW_STATE},
        )
        if result.rowcount:
            _log.warning(
                "0058 downgrade: remapped %d %s row(s) from %s to %s; these configs hold "
                "irrecoverable graph state and are now reconciler-visible again",
                result.rowcount,
                table,
                _NEW_STATE,
                _DOWNGRADE_STATE,
            )

        op.execute(f"ALTER TABLE {table} ALTER COLUMN last_build_state DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN last_build_state "
            f"TYPE graphrag_build_state USING last_build_state::graphrag_build_state"
        )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN last_build_state " f"SET DEFAULT 'idle'::graphrag_build_state"
        )
