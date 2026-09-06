"""Migration 0087's schema change and seed data, in both directions (AC-4).

WHAT THIS COSTS TO RUN, AND WHY IT IS NOT IN THE SHARED ``db`` FIXTURE
---------------------------------------------------------------------
These execute real DDL, so they cannot share the database every other ``db``-tier
test runs against. Same harness and reasoning as ``test_migration_0081_schema``: a
throwaway database named by ``SMAP_SCRATCH_DATABASE_URL``, skipped when it is
absent, and the redirect is verified rather than assumed because getting it wrong
is destructive.

WHAT THEY PIN
-------------
That the old platform-singleton unique index (``uq_prompt_assistant_config_platform``,
0042) is replaced by an "at most one *enabled* platform row" index rather than
just dropped -- multiple disabled presets must coexist, but two simultaneously
enabled ones must still be rejected at the DB level (Gap-1 resolution, see the
dossier's BOARD.md entry).

That the seed is idempotent (re-running upgrade() must not duplicate the three
preset rows) and that the seeded rows are unusable until an admin configures
them: ``enabled=false``, ``key_id=NULL``.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0087_schema.py -m db
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
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0087_prompt_assistant_persona_presets.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0087_schema", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0087 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0087)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")

_OLD_SINGLETON_INDEX = "uq_prompt_assistant_config_platform"
_ACTIVE_ONLY_INDEX = "uq_prompt_assistant_config_platform_active"


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0086."""
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
            command.upgrade(cfg, "0086_openai_compat_provider")
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _index_exists(conn: sa.engine.Connection, table: str, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_indexes WHERE tablename = :t AND indexname = :n"),
            {"t": table, "n": name},
        ).first()
    )


def _column_exists(conn: sa.engine.Connection, table: str, column: str) -> bool:
    row = conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).first()
    return row is not None


def _platform_rows(conn: sa.engine.Connection) -> list[sa.engine.Row]:
    return list(
        conn.execute(
            sa.text(
                "SELECT name, description, persona_prompt, enabled, key_id, model_id "
                "FROM prompt_assistant_configs WHERE scope = 'platform' ORDER BY name"
            )
        ).fetchall()
    )


def _begin_after_reads(conn: sa.engine.Connection) -> sa.engine.Transaction:
    conn.rollback()
    return conn.begin()


def _upgrade(conn: sa.engine.Connection) -> None:
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx), _begin_after_reads(conn):
        migration_0087.upgrade()


def test_upgrade_adds_the_three_columns(scratch_conn: sa.engine.Connection) -> None:
    for col in ("persona_prompt", "name", "description"):
        assert not _column_exists(scratch_conn, "prompt_assistant_configs", col)

    _upgrade(scratch_conn)

    for col in ("persona_prompt", "name", "description"):
        assert _column_exists(scratch_conn, "prompt_assistant_configs", col)


def test_upgrade_replaces_the_singleton_index_with_an_enabled_only_index(
    scratch_conn: sa.engine.Connection,
) -> None:
    assert _index_exists(scratch_conn, "prompt_assistant_configs", _OLD_SINGLETON_INDEX)
    assert not _index_exists(scratch_conn, "prompt_assistant_configs", _ACTIVE_ONLY_INDEX)

    _upgrade(scratch_conn)

    assert not _index_exists(scratch_conn, "prompt_assistant_configs", _OLD_SINGLETON_INDEX)
    assert _index_exists(scratch_conn, "prompt_assistant_configs", _ACTIVE_ONLY_INDEX)


def test_the_active_only_index_still_rejects_two_simultaneously_enabled_presets(
    scratch_conn: sa.engine.Connection,
) -> None:
    # The DB-level backstop behind the service's auto-disable-previous rule
    # (Gap-1 resolution): multiple disabled platform rows may coexist, but two
    # enabled at once must still violate a unique index.
    _upgrade(scratch_conn)

    trans = _begin_after_reads(scratch_conn)
    scratch_conn.execute(
        sa.text(
            "UPDATE prompt_assistant_configs SET enabled = true "
            "WHERE scope = 'platform' AND name = 'General Prompt Assistant'"
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        scratch_conn.execute(
            sa.text(
                "UPDATE prompt_assistant_configs SET enabled = true "
                "WHERE scope = 'platform' AND name = 'Creative Thinking Prompt Assistant'"
            )
        )
    trans.rollback()


def test_upgrade_seeds_three_disabled_presets_with_no_pinned_key(
    scratch_conn: sa.engine.Connection,
) -> None:
    _upgrade(scratch_conn)

    rows = _platform_rows(scratch_conn)
    assert [r.name for r in rows] == [
        "Creative Thinking Defense Prompt Assistant",
        "Creative Thinking Prompt Assistant",
        "General Prompt Assistant",
    ]
    for row in rows:
        assert row.enabled is False
        assert row.key_id is None
        assert row.model_id is None
        assert row.description != ""
        assert len(row.persona_prompt) > 100


def test_upgrade_is_idempotent_on_rerun(scratch_conn: sa.engine.Connection) -> None:
    _upgrade(scratch_conn)
    first = _platform_rows(scratch_conn)

    # Re-running upgrade() must not duplicate the seeded rows (the migration
    # guards the INSERT with WHERE NOT EXISTS ... name = :name).
    ctx = MigrationContext.configure(scratch_conn)
    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0087.upgrade()

    second = _platform_rows(scratch_conn)
    assert len(second) == 3
    assert [r.name for r in second] == [r.name for r in first]


def test_downgrade_restores_the_previous_shape(scratch_conn: sa.engine.Connection) -> None:
    _upgrade(scratch_conn)
    assert len(_platform_rows(scratch_conn)) == 3

    ctx = MigrationContext.configure(scratch_conn)
    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0087.downgrade()

    for col in ("persona_prompt", "name", "description"):
        assert not _column_exists(scratch_conn, "prompt_assistant_configs", col)
    assert _index_exists(scratch_conn, "prompt_assistant_configs", _OLD_SINGLETON_INDEX)
    assert not _index_exists(scratch_conn, "prompt_assistant_configs", _ACTIVE_ONLY_INDEX)
    remaining = scratch_conn.execute(
        sa.text("SELECT count(*) FROM prompt_assistant_configs WHERE scope = 'platform'")
    ).scalar_one()
    assert remaining == 0


def test_a_failed_upgrade_leaves_nothing_behind(
    scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0087 is a single transaction, so a failure anywhere in it must take the
    whole thing back -- the property test_migration_autocommit_ordering pins
    structurally for every migration and this pins behaviourally for this one."""
    ctx = MigrationContext.configure(scratch_conn)
    trans = _begin_after_reads(scratch_conn)
    with Operations.context(ctx):

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("index rebuild blew up mid-migration")

        # The three add_column calls run for real; the first op.execute()
        # call in upgrade() is the DROP INDEX statement -- blow up there.
        monkeypatch.setattr(migration_0087.op, "execute", _boom)
        with pytest.raises(RuntimeError, match="blew up mid-migration"):
            migration_0087.upgrade()
    trans.rollback()

    for col in ("persona_prompt", "name", "description"):
        assert not _column_exists(scratch_conn, "prompt_assistant_configs", col)
    assert _index_exists(scratch_conn, "prompt_assistant_configs", _OLD_SINGLETON_INDEX)
    assert len(_platform_rows(scratch_conn)) == 0
