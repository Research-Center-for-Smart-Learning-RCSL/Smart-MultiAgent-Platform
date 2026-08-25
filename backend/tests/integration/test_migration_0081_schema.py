"""Migration 0081's schema change, in both directions.

WHAT THIS COSTS TO RUN, AND WHY IT IS NOT IN THE SHARED ``db`` FIXTURE
---------------------------------------------------------------------
These execute real DDL, so they cannot share the database every other ``db``-tier
test runs against. Same harness and same reasoning as
``test_migration_0077_schema``: a throwaway database named by
``SMAP_SCRATCH_DATABASE_URL``, skipped when it is absent, and the redirect is
verified rather than assumed because getting it wrong is destructive.

WHAT THEY PIN
-------------
That the CHECK lands in the SAME migration that relaxes the NOT NULL. A window in
which a session may legally have no subject at all is a window in which one gets
written, and "the constraint arrives in 0082" is exactly the kind of decision that
looks harmless in a diff.

And that both of 0077's indexes survive: ``uq_activity_sessions_open`` and
``uq_activity_sessions_activation_subject`` key on ``subject_user_id``, which a
group session leaves NULL, so neither can see the group population and neither
needed changing. A later reader tidying up needs that defended by a test.

The downgrade is asserted too, including the part that is NOT lossless: a group
session cannot exist under the restored NOT NULL, so ``downgrade()`` deletes those
rows rather than aborting against the first one.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0081_schema.py -m db
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

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0081_group_activity_submissions.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0081_schema", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0081 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0081)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")

_LEGACY_OPEN_INDEX = "uq_activity_sessions_open"
_SUBJECT_INDEX = "uq_activity_sessions_activation_subject"
_GROUP_INDEX = "uq_activity_sessions_activation_group"
_ONE_SUBJECT_CHECK = "ck_activity_sessions_one_subject"


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0080.

    The redirect goes through ``SMAP_DB_DSN`` because ``alembic/env.py`` always
    builds its own engine from ``get_settings().database.dsn`` -- see
    ``test_migration_0076_atomicity`` for the full account. The resolved DSN is
    asserted before any DDL runs, because the failure mode of getting it wrong is
    rewriting the shared database's schema.
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
            command.upgrade(cfg, "0080_observation_presentation_blocks")
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


def _column_is_nullable(conn: sa.engine.Connection, table: str, column: str) -> bool | None:
    row = conn.execute(
        sa.text(
            "SELECT is_nullable FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return None if row is None else row.is_nullable == "YES"


def _constraint_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(conn.execute(sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}).first())


def _table_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :n"), {"n": name}
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


def _upgrade(conn: sa.engine.Connection) -> None:
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx), _begin_after_reads(conn):
        migration_0081.upgrade()


def test_the_upgrade_relaxes_the_not_null_and_adds_the_check(
    scratch_conn: sa.engine.Connection,
) -> None:
    assert _column_is_nullable(scratch_conn, "activity_sessions", "subject_user_id") is False
    assert not _constraint_exists(scratch_conn, _ONE_SUBJECT_CHECK)

    _upgrade(scratch_conn)

    assert _column_is_nullable(scratch_conn, "activity_sessions", "subject_user_id") is True
    assert _column_is_nullable(scratch_conn, "activity_sessions", "subject_member_group_id") is True
    # The pair is the point: a relaxed NOT NULL without its replacement is the
    # one intermediate state this migration must never leave behind.
    assert _constraint_exists(scratch_conn, _ONE_SUBJECT_CHECK)
    assert _index_exists(scratch_conn, "activity_sessions", _GROUP_INDEX)
    assert _column_is_nullable(scratch_conn, "activity_types", "group_config") is True


def test_the_upgrade_leaves_both_user_keyed_uniques_in_place(
    scratch_conn: sa.engine.Connection,
) -> None:
    """Neither can see the group population, so neither needed changing.

    Both key on ``subject_user_id``, which a group session leaves NULL, and
    PostgreSQL treats NULLs as distinct in a unique index. Dropping either as
    "now redundant" would reopen the races 0049 and 0077 closed.
    """
    _upgrade(scratch_conn)

    assert _index_exists(scratch_conn, "activity_sessions", _LEGACY_OPEN_INDEX)
    assert _index_exists(scratch_conn, "activity_sessions", _SUBJECT_INDEX)


def test_the_upgrade_creates_the_proposal_tables(scratch_conn: sa.engine.Connection) -> None:
    assert not _table_exists(scratch_conn, "activity_group_proposals")

    _upgrade(scratch_conn)

    assert _table_exists(scratch_conn, "activity_group_proposals")
    assert _table_exists(scratch_conn, "activity_group_proposal_votes")
    assert _index_exists(scratch_conn, "activity_group_proposals", "uq_activity_group_proposals_open")
    assert _index_exists(scratch_conn, "activity_group_proposals", "ix_activity_group_proposals_expiry")


def test_the_downgrade_restores_the_previous_shape(scratch_conn: sa.engine.Connection) -> None:
    _upgrade(scratch_conn)
    assert _constraint_exists(scratch_conn, _ONE_SUBJECT_CHECK)

    ctx = MigrationContext.configure(scratch_conn)
    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0081.downgrade()

    assert _column_is_nullable(scratch_conn, "activity_sessions", "subject_user_id") is False
    assert _column_is_nullable(scratch_conn, "activity_sessions", "subject_member_group_id") is None
    assert not _constraint_exists(scratch_conn, _ONE_SUBJECT_CHECK)
    assert not _table_exists(scratch_conn, "activity_group_proposals")
    assert not _table_exists(scratch_conn, "activity_group_proposal_votes")
    assert _column_is_nullable(scratch_conn, "activity_types", "group_config") is None


def test_the_downgrade_clears_group_sessions_rather_than_aborting(
    scratch_conn: sa.engine.Connection,
) -> None:
    """The documented non-lossless half, executed rather than described.

    A group session cannot exist under the restored NOT NULL. Deleting them
    explicitly is what stops ``alter_column`` failing against the first one and
    leaving the schema half-reverted -- and the personal session beside it must
    survive, which is the assertion that makes the delete a scalpel rather than
    a truncate.
    """
    _upgrade(scratch_conn)

    project_id, user_id = uuid.uuid4(), uuid.uuid4()
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id = uuid.uuid4(), uuid.uuid4()
    personal_id, group_id = uuid.uuid4(), uuid.uuid4()

    trans = _begin_after_reads(scratch_conn)
    scratch_conn.execute(
        sa.text("INSERT INTO users (id, email, password_hash) VALUES (:i, :e, 'x')"),
        {"i": user_id, "e": f"{user_id}@example.test"},
    )
    # No org row: `projects.org_id` is nullable (a user-owned project), which is
    # the same shortcut the shared `project` fixture takes.
    scratch_conn.execute(
        sa.text(
            "INSERT INTO projects (id, name, owner_user_id, created_by_user_id) VALUES (:i, 'p', :u, :u)"
        ),
        {"i": project_id, "u": user_id},
    )
    scratch_conn.execute(
        sa.text("INSERT INTO workspaces (id, project_id, name) VALUES (:i, :p, 'w')"),
        {"i": workspace_id, "p": project_id},
    )
    scratch_conn.execute(
        sa.text(
            "INSERT INTO chatrooms (id, workspace_id, name, guest_token, created_by_user_id) "
            "VALUES (:i, :w, 'c', :g, :u)"
        ),
        {"i": chatroom_id, "w": workspace_id, "g": str(uuid.uuid4()), "u": user_id},
    )
    scratch_conn.execute(
        sa.text(
            "INSERT INTO activity_types (id, project_id, key, name, validator_kind) "
            "VALUES (:i, :p, 'k', 'n', 'in_process')"
        ),
        {"i": type_id, "p": project_id},
    )
    scratch_conn.execute(
        sa.text(
            "INSERT INTO activity_activations (id, chatroom_id, activity_type_id, started_by_user_id) "
            "VALUES (:i, :c, :t, :u)"
        ),
        {"i": activation_id, "c": chatroom_id, "t": type_id, "u": user_id},
    )
    for sid, column, value in (
        (personal_id, "subject_user_id", user_id),
        (group_id, "subject_member_group_id", uuid.uuid4()),
    ):
        scratch_conn.execute(
            sa.text(
                "INSERT INTO activity_sessions "
                f"(id, activity_type_id, chatroom_id, activation_id, {column}) "
                "VALUES (:i, :t, :c, :a, :v)"
            ),
            {"i": sid, "t": type_id, "c": chatroom_id, "a": activation_id, "v": value},
        )
    trans.commit()

    ctx = MigrationContext.configure(scratch_conn)
    with Operations.context(ctx), _begin_after_reads(scratch_conn):
        migration_0081.downgrade()

    remaining = {
        row.id for row in scratch_conn.execute(sa.text("SELECT id FROM activity_sessions")).fetchall()
    }
    assert remaining == {personal_id}


def test_a_failed_upgrade_leaves_nothing_behind(
    scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0081 is a single transaction in both directions, so a failure anywhere in
    it must take the whole thing back -- the property
    ``test_migration_autocommit_ordering`` pins structurally for every migration
    and this pins behaviourally for this one."""
    ctx = MigrationContext.configure(scratch_conn)
    trans = _begin_after_reads(scratch_conn)
    with Operations.context(ctx):

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("index build blew up mid-migration")

        # The first `op.execute` is the group unique, after both columns, the
        # relaxed NOT NULL and the CHECK.
        monkeypatch.setattr(migration_0081.op, "execute", _boom)
        with pytest.raises(RuntimeError, match="blew up"):
            migration_0081.upgrade()
    trans.rollback()

    assert _column_is_nullable(scratch_conn, "activity_sessions", "subject_user_id") is False
    assert _column_is_nullable(scratch_conn, "activity_sessions", "subject_member_group_id") is None
    assert not _constraint_exists(scratch_conn, _ONE_SUBJECT_CHECK)
    assert not _table_exists(scratch_conn, "activity_group_proposals")
