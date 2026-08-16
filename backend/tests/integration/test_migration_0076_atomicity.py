"""Migration 0076 applies atomically in both directions, or not at all.

WHAT THIS COSTS TO RUN, AND WHY IT IS NOT IN THE SHARED ``db`` FIXTURE
---------------------------------------------------------------------
These tests execute the migration's real DDL, so they cannot share the database
every other ``db``-tier test is running against -- ``test_platform_activity_type_schema``
says the same thing and declines to execute DDL for exactly this reason. They take
their own connection to a scratch database named by ``SMAP_SCRATCH_DATABASE_URL``
and skip when it is absent, so a normal ``db``-tier run is unaffected.

WHAT THEY PIN
-------------
Before the fix, 0076 issued four DDL statements and then opened
``op.get_context().autocommit_block()``. That block unconditionally commits the
transaction preceding it, while the revision stamp is written only after the
migration body returns -- so a failure in the concurrent index build or in
``create_table`` left ``scope``, a nullable ``project_id`` and both CHECK
constraints committed while ``alembic_version`` still read ``0075``. Re-running
died on ``ADD COLUMN scope`` with ``DuplicateColumn`` and the operator had to
hand-drop three objects before the deploy could move.

``downgrade()`` had the same defect mirrored: it dropped the index and the opt-in
table, then opened a block, so a failure below that point left the table gone and
committed at version 0076.

The unit tier cannot see any of this -- it compiles statements and never executes
one, so "the block committed" and "the block did not commit" are indistinguishable
there. ``tests/unit/test_migration_autocommit_ordering.py`` pins the *structural*
rule that prevents the class; these pin the *behaviour* for this instance.

HOW TO RUN THEM
---------------
Point ``SMAP_SCRATCH_DATABASE_URL`` at a **dedicated throwaway database** -- the
fixture drops and recreates its ``public`` schema. Without it they skip.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0076_atomicity.py -m db

WHY THE SKIP IS THE DANGEROUS PART
----------------------------------
These gate themselves on an environment variable, and for their first life nothing
set it: CI's ``backend-db`` job reported ``68 passed, 5 skipped`` over a run in
which neither of these executed, which reads as coverage. ``ci.yml`` now creates a
``smap_scratch`` database on the postgres service that job already starts. If that
step is ever removed, these go quiet again rather than failing.
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
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0076_platform_activity_types.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0076_atomicity", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0076 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0076)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0075.

    HOW THE REDIRECT WORKS, AND WHY IT IS ASSERTED RATHER THAN ASSUMED
    ------------------------------------------------------------------
    The obvious idiom -- ``cfg.attributes["connection"]`` -- does **not** work in
    this project: ``run_migrations_online`` (``alembic/env.py:129-146``) always
    builds its own engine from ``_sync_dsn()``, which reads
    ``get_settings().database.dsn`` (``env.py:100-105``) and ignores any injected
    connection. A fixture written that way would silently run destructive DDL
    against the shared ``db``-tier database.

    So the redirect goes through the only channel ``env.py`` actually reads: the
    ``SMAP_DB_DSN`` environment variable behind ``get_settings()``, whose
    ``lru_cache`` is cleared on both sides of the override. Because getting this
    wrong is destructive rather than merely wrong, the fixture **verifies** that
    ``env._sync_dsn()`` now resolves to the scratch database and aborts if it does
    not -- a guard, not a comment.
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
        # Assert on the *input* env.py reads rather than importing env.py to call
        # its _sync_dsn(): that module runs a migration at import time
        # (`alembic/env.py:149-152`), so importing it here would either fire
        # run_migrations_online() or raise for want of an alembic context.
        configured = get_settings().database.dsn
        if configured != _SCRATCH_URL:
            pytest.fail(
                "refusing to run: the DSN override did not take effect. Alembic would migrate "
                f"{configured!r}, not the scratch database {_SCRATCH_URL!r}. Running anyway "
                "would rewrite the schema of whatever that first URL points at."
            )

        # The same conversion env.py:100-105 applies, so this engine and Alembic's
        # own necessarily address one database.
        engine = sa.create_engine(_SCRATCH_URL.replace("+asyncpg", "+psycopg"))
        try:
            # A dedicated scratch database, verified above -- so starting from a
            # known-empty schema is safe and makes each test independent.
            with engine.begin() as reset:
                reset.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
                reset.execute(sa.text("CREATE SCHEMA public"))
            _upgrade_to_0075()
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _upgrade_to_0075() -> None:
    """Run the chain against whatever ``env.py`` resolves -- the caller has already
    verified that is the scratch database."""
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(cfg, "0075_activity_policies")


def _scope_column_exists(conn: sa.engine.Connection) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'activity_types' AND column_name = 'scope'"
            )
        ).first()
    )


def _raise(message: str) -> object:
    """A stand-in for an ``op.*`` call that blows up mid-migration."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(message)

    return _boom


def _table_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :n"), {"n": name}
        ).first()
    )


def _begin_after_reads(conn: sa.engine.Connection) -> sa.engine.Transaction:
    """Start the explicit transaction the migration runs in.

    SQLAlchemy 2.0 *autobegins* on the first ``execute``, so the schema
    assertions above each call site already own a transaction and a bare
    ``conn.begin()`` raises ``InvalidRequestError``. Every read on this
    connection is an assertion, so discarding that implicit transaction is
    safe -- and it must be discarded rather than committed, since the whole
    subject of these tests is what survives a rollback.
    """
    conn.rollback()
    return conn.begin()


class TestUpgradeAtomicity:
    def test_a_failure_after_the_index_leaves_no_scope_column(
        self, scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline regression: schema must not outlive a failed run.

        Fails against the pre-fix migration, where the autocommit block had already
        committed ``scope``, the nullable ``project_id`` and both CHECKs by the time
        ``create_table`` ran.
        """
        # The fixture already migrated the scratch database to 0075.
        assert not _scope_column_exists(scratch_conn)

        trans = _begin_after_reads(scratch_conn)
        ctx = MigrationContext.configure(scratch_conn)
        with Operations.context(ctx):
            monkeypatch.setattr(
                migration_0076.op, "create_table", _raise("create_table blew up mid-migration")
            )
            with pytest.raises(RuntimeError, match="blew up"):
                migration_0076.upgrade()
        trans.rollback()

        # Before the fix the autocommit block had already committed `scope`, the
        # nullable `project_id` and both CHECKs by this point, so the rollback
        # could not take them back and these assertions failed.
        assert not _scope_column_exists(scratch_conn)
        assert not _table_exists(scratch_conn, "project_activity_type_optins")


class TestDowngradeAtomicity:
    def test_a_failure_after_the_table_drop_leaves_the_optin_table(
        self, scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mirrored defect the audit missed and the structural test found."""
        # Apply 0076 for real first, so the downgrade has something to reverse.
        ctx = MigrationContext.configure(scratch_conn)
        with Operations.context(ctx), _begin_after_reads(scratch_conn):
            migration_0076.upgrade()
        assert _table_exists(scratch_conn, "project_activity_type_optins")

        trans = _begin_after_reads(scratch_conn)
        with Operations.context(ctx):
            monkeypatch.setattr(migration_0076.op, "drop_constraint", _raise("drop_constraint blew up"))
            with pytest.raises(RuntimeError, match="blew up"):
                migration_0076.downgrade()
        trans.rollback()

        # Before the fix the block had already committed both drops by this point,
        # leaving the opt-in table gone at version 0076.
        assert _table_exists(scratch_conn, "project_activity_type_optins")
