"""Migration 0078 applies atomically in both directions, or not at all.

The same discipline `2026-08-16-migration-0076-retry-safety` established, applied
to the migration this feature adds. 0078 touches two tables and creates two CHECK
constraints; a partial application would leave `chatroom_agents` carrying columns
with no constraints while `alembic_version` still read 0077, and the re-run would
die on `ADD COLUMN may_control_activities` with `DuplicateColumn`.

0078 has no autocommit block and no CONCURRENTLY, so it *should* be one
transaction by construction. That is exactly the claim worth executing rather than
reading: `tests/unit/test_migration_autocommit_ordering.py` pins the structural
rule that keeps the class of defect away, and this pins the behaviour for this
instance — a later edit that adds a block would fail here rather than in
production.

Gated on ``SMAP_SCRATCH_DATABASE_URL`` like every migration test in this tier;
see ``test_migration_0076_atomicity`` for what the variable is for, how the DSN
redirect is verified rather than assumed, and why a silent skip is the dangerous
failure mode.
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
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0078_agent_delegated_activity_control.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0078_atomicity", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0078 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0078)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0077."""
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
                f"{configured!r}, not the scratch database {_SCRATCH_URL!r}. Running anyway "
                "would rewrite the schema of whatever that first URL points at."
            )

        engine = sa.create_engine(_SCRATCH_URL.replace("+asyncpg", "+psycopg"))
        try:
            with engine.begin() as reset:
                reset.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
                reset.execute(sa.text("CREATE SCHEMA public"))
            cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(cfg, "0077_activity_session_activation")
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _column_exists(conn: sa.engine.Connection, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
            {"t": table, "c": column},
        ).first()
    )


def _constraint_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(conn.execute(sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}).first())


def _raise(message: str) -> object:
    """A stand-in for an ``op.*`` call that blows up mid-migration."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(message)

    return _boom


def _begin_after_reads(conn: sa.engine.Connection) -> sa.engine.Transaction:
    """Start the explicit transaction the migration runs in.

    SQLAlchemy 2.0 autobegins on the first ``execute``, so the schema assertions
    above each call site already own a transaction and a bare ``conn.begin()``
    raises. Every read here is an assertion, so discarding that implicit
    transaction is safe — and it must be discarded rather than committed, since
    what survives a rollback is the whole subject.
    """
    conn.rollback()
    return conn.begin()


class TestUpgradeAtomicity:
    def test_a_failure_at_the_last_column_leaves_nothing_behind(
        self, scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The columns and constraints added before the failure must not survive it.

        ``add_column`` is patched to raise on its *last* call, which is the
        ``activity_activations`` one — so by the time it fires, three columns and
        both CHECK constraints have already been issued on ``chatroom_agents``.
        """
        assert not _column_exists(scratch_conn, "chatroom_agents", "may_control_activities")

        real_add_column = migration_0078.op.add_column
        calls = {"n": 0}

        def _add_column(*args: object, **kwargs: object) -> None:
            calls["n"] += 1
            if calls["n"] == 4:
                raise RuntimeError("add_column blew up mid-migration")
            real_add_column(*args, **kwargs)

        trans = _begin_after_reads(scratch_conn)
        ctx = MigrationContext.configure(scratch_conn)
        with Operations.context(ctx):
            monkeypatch.setattr(migration_0078.op, "add_column", _add_column)
            with pytest.raises(RuntimeError, match="blew up"):
                migration_0078.upgrade()
        trans.rollback()

        assert calls["n"] == 4  # the failure really was the last statement, not the first
        assert not _column_exists(scratch_conn, "chatroom_agents", "may_control_activities")
        assert not _column_exists(scratch_conn, "chatroom_agents", "activity_type_allowlist")
        assert not _column_exists(scratch_conn, "chatroom_agents", "granted_by_user_id")
        assert not _column_exists(scratch_conn, "activity_activations", "started_by_agent_id")
        assert not _constraint_exists(scratch_conn, migration_0078.GRANT_CHECK_NAME)


class TestDowngradeAtomicity:
    def test_a_failure_mid_downgrade_leaves_the_schema_whole(
        self, scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = MigrationContext.configure(scratch_conn)
        with Operations.context(ctx), _begin_after_reads(scratch_conn):
            migration_0078.upgrade()
        assert _column_exists(scratch_conn, "chatroom_agents", "may_control_activities")
        assert _constraint_exists(scratch_conn, migration_0078.GRANT_CHECK_NAME)

        trans = _begin_after_reads(scratch_conn)
        with Operations.context(ctx):
            monkeypatch.setattr(migration_0078.op, "drop_column", _raise("drop_column blew up"))
            with pytest.raises(RuntimeError, match="blew up"):
                migration_0078.downgrade()
        trans.rollback()

        # The constraint dropped before the failure came back with the rollback.
        assert _constraint_exists(scratch_conn, migration_0078.GRANT_CHECK_NAME)
        assert _column_exists(scratch_conn, "activity_activations", "started_by_agent_id")

    def test_the_downgrade_reverses_the_upgrade_completely(self, scratch_conn: sa.engine.Connection) -> None:
        """Nothing is transformed in either direction, so the round trip is exact."""
        ctx = MigrationContext.configure(scratch_conn)
        with Operations.context(ctx), _begin_after_reads(scratch_conn):
            migration_0078.upgrade()
        with Operations.context(ctx), _begin_after_reads(scratch_conn):
            migration_0078.downgrade()

        assert not _column_exists(scratch_conn, "chatroom_agents", "may_control_activities")
        assert not _column_exists(scratch_conn, "chatroom_agents", "activity_type_allowlist")
        assert not _column_exists(scratch_conn, "chatroom_agents", "granted_by_user_id")
        assert not _column_exists(scratch_conn, "activity_activations", "started_by_agent_id")
        assert not _constraint_exists(scratch_conn, migration_0078.GRANT_CHECK_NAME)
