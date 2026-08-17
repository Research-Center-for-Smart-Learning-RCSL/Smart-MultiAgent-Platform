"""Migration 0077 swaps the session uniqueness rule, in both directions (AC-9).

WHAT THIS COSTS TO RUN, AND WHY IT IS NOT IN THE SHARED ``db`` FIXTURE
---------------------------------------------------------------------
These execute real DDL, so they cannot share the database every other ``db``-tier
test runs against. Same harness and same reasoning as
``test_migration_0076_atomicity``: a throwaway database named by
``SMAP_SCRATCH_DATABASE_URL``, skipped when it is absent, and the redirect is
verified rather than assumed because getting it wrong is destructive.

WHAT THEY PIN
-------------
The index swap is the load-bearing half of 0077. ``uq_activity_sessions_open``
was ``(activity_type_id, chatroom_id, subject_user_id) WHERE status = 'open'``,
which forbids a subject a second round's session while the first is still open --
the exact reason a re-run of one activity used to reuse the previous round's
session and continue its attempt sequence. It has to be *gone*, and the plain
``(activation_id, subject_user_id)`` unique has to be *there*; either half
missing leaves the defect in place, and neither is visible to a tier that never
executes DDL.

The downgrade is asserted too, because it is not a mechanical reversal: it has to
retire duplicate open sessions before it can restore a constraint that forbids
them. This file exercises the empty-table path (structure only); the data half
lives in ``test_activity_session_activation.py``, which runs the migration's own
backfill SQL against real rows without touching the schema.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0077_index_swap.py -m db
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alembic import command

pytestmark = pytest.mark.db

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0077_activity_session_activation.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0077_index_swap", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0077 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0077)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")

_OLD_INDEX = "uq_activity_sessions_open"
_NEW_INDEX = "uq_activity_sessions_activation_subject"


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0076.

    The redirect goes through ``SMAP_DB_DSN`` because ``alembic/env.py`` always
    builds its own engine from ``get_settings().database.dsn`` and ignores an
    injected connection -- see ``test_migration_0076_atomicity`` for the full
    account. The resolved DSN is asserted before any DDL runs, because the
    failure mode of getting it wrong is rewriting the shared database's schema.
    """
    if not _SCRATCH_URL:
        pytest.skip(
            "SMAP_SCRATCH_DATABASE_URL is not set. These tests migrate and drop schema, "
            "so they require a dedicated throwaway database, never the db-tier one."
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
            command.upgrade(cfg, "0076_platform_activity_types")
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _index_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE tablename = 'activity_sessions' AND indexname = :n"),
            {"n": name},
        ).first()
    )


def _column_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'activity_sessions' AND column_name = :n"
            ),
            {"n": name},
        ).first()
    )


def _begin_after_reads(conn: sa.engine.Connection) -> sa.engine.Transaction:
    """Start the explicit transaction the migration runs in.

    SQLAlchemy 2.0 autobegins on the first ``execute``, so the schema assertions
    above each call site already own a transaction and a bare ``conn.begin()``
    raises. Every read here is an assertion, so discarding that implicit
    transaction is safe.
    """
    conn.rollback()
    return conn.begin()


def test_the_upgrade_replaces_the_open_partial_unique(scratch_conn: sa.engine.Connection) -> None:
    assert _index_exists(scratch_conn, _OLD_INDEX)
    assert not _column_exists(scratch_conn, "activation_id")

    ctx = MigrationContext.configure(scratch_conn)
    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0077.upgrade()

    assert _column_exists(scratch_conn, "activation_id")
    assert _column_exists(scratch_conn, "completed_at")
    assert _index_exists(scratch_conn, _NEW_INDEX)
    # The half that is easy to forget: leaving the old index in place would keep
    # forbidding the second round's session, so the migration would apply
    # cleanly and change nothing about the defect.
    assert not _index_exists(scratch_conn, _OLD_INDEX)


def test_the_downgrade_restores_the_previous_shape(scratch_conn: sa.engine.Connection) -> None:
    ctx = MigrationContext.configure(scratch_conn)
    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0077.upgrade()
    assert _index_exists(scratch_conn, _NEW_INDEX)

    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0077.downgrade()

    assert _index_exists(scratch_conn, _OLD_INDEX)
    assert not _index_exists(scratch_conn, _NEW_INDEX)
    assert not _column_exists(scratch_conn, "activation_id")
    assert not _column_exists(scratch_conn, "completed_at")


def test_a_failed_upgrade_leaves_no_column_behind(
    scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0077 is a single transaction in both directions, so a failure anywhere in
    it must take the whole thing back -- the property
    ``test_migration_autocommit_ordering`` pins structurally for every migration
    and this pins behaviourally for this one."""
    ctx = MigrationContext.configure(scratch_conn)
    trans = _begin_after_reads(scratch_conn)
    with Operations.context(ctx):
        calls = {"n": 0}

        def _boom(*_args: object, **_kwargs: object) -> None:
            calls["n"] += 1
            # Fail on the index swap, after both columns and both data steps.
            if calls["n"] >= 3:
                raise RuntimeError("index swap blew up mid-migration")

        monkeypatch.setattr(migration_0077.op, "execute", _boom)
        with pytest.raises(RuntimeError, match="blew up"):
            migration_0077.upgrade()
    trans.rollback()

    assert not _column_exists(scratch_conn, "activation_id")
    assert not _column_exists(scratch_conn, "completed_at")
    assert _index_exists(scratch_conn, _OLD_INDEX)
