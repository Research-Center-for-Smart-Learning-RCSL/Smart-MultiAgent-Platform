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

STATUS: WRITTEN, NEVER EXECUTED
-------------------------------
Docker was unavailable on the implementing host, so no ``db``-tier test could run
and these have only ever been collected. They are the empirical half of AC-1 and
AC-2 and both remain unverified until CI or a developer with a running PostgreSQL
executes them. Do not read a green unit run as covering them.
"""

from __future__ import annotations

import importlib.util
import os
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
def scratch_conn() -> sa.engine.Connection:
    """A connection to a throwaway database at revision 0075.

    DISABLED -- DO NOT RE-ENABLE WITHOUT READING THIS.

    ``_upgrade_to_0075`` below sets ``cfg.attributes["connection"]``, which is the
    standard Alembic idiom for driving a migration against a caller-supplied
    connection. **This project's ``env.py`` does not honour it.**
    ``run_migrations_online`` (``alembic/env.py:129-146``) unconditionally builds
    its own engine from ``_sync_dsn()``, which reads ``get_settings().database.dsn``
    (``env.py:100-105``) -- so the injected connection is ignored and the migration
    chain runs against the *configured* database.

    That makes these tests worse than useless: setting SMAP_SCRATCH_DATABASE_URL
    would not redirect them to the scratch database, it would run destructive DDL
    against whatever the shared ``db``-tier settings point at. This is precisely
    the hazard ``test_platform_activity_type_schema.py`` declines to take.

    The skip is therefore unconditional and deliberate, pending a decision on
    whether to (a) drop these in favour of the structural unit test, which already
    catches the regression class deterministically, or (b) rework the fixture to
    override the settings DSN. See the dossier's Deviation Log.
    """
    pytest.skip(
        "disabled: env.py ignores cfg.attributes['connection'], so this fixture cannot "
        "target a scratch database -- see the module and fixture docstrings"
    )


def _upgrade_to_0075(conn: sa.engine.Connection) -> None:
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.attributes["connection"] = conn
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


def _table_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :n"), {"n": name}
        ).first()
    )


class TestUpgradeAtomicity:
    def test_a_failure_after_the_index_leaves_no_scope_column(
        self, scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline regression: schema must not outlive a failed run.

        Fails against the pre-fix migration, where the autocommit block had already
        committed ``scope``, the nullable ``project_id`` and both CHECKs by the time
        ``create_table`` ran.
        """
        _upgrade_to_0075(scratch_conn)
        assert not _scope_column_exists(scratch_conn)

        ctx = MigrationContext.configure(scratch_conn)
        with Operations.context(ctx):
            monkeypatch.setattr(
                migration_0076.op, "create_table", _raise("create_table blew up mid-migration")
            )
            with pytest.raises(RuntimeError, match="blew up"):
                migration_0076.upgrade()

        # The transaction the fixture opened is still open and will roll back; the
        # point is that nothing was committed out from under it.
        scratch_conn.rollback()
        assert not _scope_column_exists(scratch_conn)
        assert not _table_exists(scratch_conn, "project_activity_type_optins")


class TestDowngradeAtomicity:
    def test_a_failure_after_the_table_drop_leaves_the_optin_table(
        self, scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mirrored defect the audit missed and the structural test found."""
        _upgrade_to_0075(scratch_conn)
        ctx = MigrationContext.configure(scratch_conn)
        with Operations.context(ctx):
            migration_0076.upgrade()
            assert _table_exists(scratch_conn, "project_activity_type_optins")

            monkeypatch.setattr(migration_0076.op, "drop_constraint", _raise("drop_constraint blew up"))
            with pytest.raises(RuntimeError, match="blew up"):
                migration_0076.downgrade()

        scratch_conn.rollback()
        assert _table_exists(scratch_conn, "project_activity_type_optins")


def _raise(message: str):
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(message)

    return _boom
