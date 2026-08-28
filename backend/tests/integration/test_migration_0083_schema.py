"""Migration 0083's `agent_effort` widening, in both directions (AC-12).

Same throwaway-database harness as ``test_migration_0081_schema``: real DDL
against a real PostgreSQL enum, which the unit tier's ``literal_binds``
compilation cannot see (`backend/CLAUDE.md`'s "PostgreSQL-specific SQL needs a
db-tier test" — an enum's actual member set is exactly that kind of fact).

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0083_schema.py -m db
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alembic import command

pytestmark = pytest.mark.db

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0083_widen_agent_effort.py"
_spec = importlib.util.spec_from_file_location("_migration_0083_schema", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0083 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0083)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")

_WIDENED_VALUES = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_ORIGINAL_VALUES = {"low", "medium", "high"}


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0082."""
    if not _SCRATCH_URL:
        pytest.skip(
            "SMAP_SCRATCH_DATABASE_URL is not set. This test alters a real PostgreSQL enum, so "
            "it requires a dedicated throwaway database, never the db-tier one."
        )

    from app.config.settings import get_settings

    monkeypatch.setenv("SMAP_DB_DSN", _SCRATCH_URL)
    get_settings.cache_clear()
    try:
        configured = get_settings().database.dsn
        if configured != _SCRATCH_URL:
            pytest.fail(
                "refusing to run: the DSN override did not take effect. Alembic would migrate "
                f"{configured!r}, not the scratch database {_SCRATCH_URL!r}."
            )

        engine = sa.create_engine(_SCRATCH_URL.replace("+asyncpg", "+psycopg"))
        try:
            with engine.begin() as reset:
                reset.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
                reset.execute(sa.text("CREATE SCHEMA public"))
            cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(cfg, "0082_live_draft_grant_and_disclosure")
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _enum_values(conn: sa.engine.Connection, type_name: str) -> set[str]:
    rows = conn.execute(
        sa.text("SELECT enumlabel FROM pg_enum WHERE enumtypid = CAST(:t AS regtype)"), {"t": type_name}
    ).fetchall()
    return {r.enumlabel for r in rows}


def _begin_after_reads(conn: sa.engine.Connection) -> sa.engine.Transaction:
    conn.rollback()
    return conn.begin()


def _upgrade(conn: sa.engine.Connection) -> None:
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx), _begin_after_reads(conn):
        migration_0083.upgrade()


def test_the_upgrade_widens_the_enum_to_the_full_union(scratch_conn: sa.engine.Connection) -> None:
    assert _enum_values(scratch_conn, "agent_effort") == _ORIGINAL_VALUES

    _upgrade(scratch_conn)

    assert _enum_values(scratch_conn, "agent_effort") == _WIDENED_VALUES


def _seed_agent(conn: sa.engine.Connection, *, effort: str | None) -> uuid.UUID:
    user_id, project_id, kg_id, agent_id = (uuid.uuid4() for _ in range(4))
    conn.execute(
        sa.text("INSERT INTO users (id, email, password_hash) VALUES (:i, :e, 'x')"),
        {"i": user_id, "e": f"{user_id}@example.test"},
    )
    conn.execute(
        sa.text(
            "INSERT INTO projects (id, name, owner_user_id, created_by_user_id) VALUES (:i, 'p', :u, :u)"
        ),
        {"i": project_id, "u": user_id},
    )
    conn.execute(
        sa.text("INSERT INTO key_groups (id, project_id, name) VALUES (:i, :p, 'kg')"),
        {"i": kg_id, "p": project_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO agents (id, project_id, name, model_hint, key_group_id, effort) "
            "VALUES (:i, :p, 'a', 'openai', :kg, :effort)"
        ),
        {"i": agent_id, "p": project_id, "kg": kg_id, "effort": effort},
    )
    return agent_id


def test_the_downgrade_nulls_a_row_holding_a_new_value_rather_than_failing(
    scratch_conn: sa.engine.Connection,
) -> None:
    """§10's documented lossy downgrade, executed rather than described.

    `ALTER TYPE ... DROP VALUE` does not exist in PostgreSQL, so shrinking the
    enum means recreating it under the original name -- which fails outright
    if any row holds a value the original three cannot represent. The
    downgrade nulls those rows first; this is the difference between that and
    a downgrade that aborts partway with the schema in neither state.
    """
    _upgrade(scratch_conn)
    minimal_agent = _seed_agent(scratch_conn, effort="minimal")
    high_agent = _seed_agent(scratch_conn, effort="high")
    unset_agent = _seed_agent(scratch_conn, effort=None)
    scratch_conn.commit()

    ctx = MigrationContext.configure(scratch_conn)
    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0083.downgrade()

    assert _enum_values(scratch_conn, "agent_effort") == _ORIGINAL_VALUES
    rows = {r.id: r.effort for r in scratch_conn.execute(sa.text("SELECT id, effort FROM agents")).fetchall()}
    # The value the original enum cannot represent is nulled -- inert, not lost
    # data the platform depended on (FU-3): every other column on that agent
    # row survives untouched.
    assert rows[minimal_agent] is None
    # A value the original enum still has is preserved exactly.
    assert rows[high_agent] == "high"
    assert rows[unset_agent] is None
