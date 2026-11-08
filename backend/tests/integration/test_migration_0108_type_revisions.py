"""Migration 0108's seed and backfill, in both directions (AC-12).

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5 and Q-7.

Same harness as ``test_migration_0081_schema``: a throwaway database named by
``SMAP_SCRATCH_DATABASE_URL``, skipped when absent, with the DSN redirect
verified before any DDL runs.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0108_type_revisions.py -m db
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from alembic import command

pytestmark = pytest.mark.db

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0108_activity_type_revisions.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0108", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0108 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0108)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0107."""
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
            command.upgrade(cfg, "0107_class_closed_at")
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _begin_after_reads(conn: sa.engine.Connection) -> sa.engine.Transaction:
    conn.rollback()
    return conn.begin()


def _run(conn: sa.engine.Connection, step: str) -> None:
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx), _begin_after_reads(conn):
        getattr(migration_0108, step)()


def _table_exists(conn: sa.engine.Connection, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :n"), {"n": name}
        ).first()
    )


def _column_exists(conn: sa.engine.Connection, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
            {"t": table, "c": column},
        ).first()
    )


@dataclass(frozen=True)
class Seeded:
    unbumped_type: uuid.UUID
    bumped_type: uuid.UUID
    unbumped_submission: uuid.UUID
    bumped_submission: uuid.UUID


def _seed(conn: sa.engine.Connection) -> Seeded:
    """A version-1 type and a version-3 type, each with one submission."""
    user_id, project_id = uuid.uuid4(), uuid.uuid4()
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    seeded = Seeded(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    trans = _begin_after_reads(conn)
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
        sa.text("INSERT INTO workspaces (id, project_id, name) VALUES (:i, :p, 'w')"),
        {"i": workspace_id, "p": project_id},
    )
    conn.execute(
        sa.text(
            "INSERT INTO chatrooms (id, workspace_id, name, guest_token, created_by_user_id) "
            "VALUES (:i, :w, 'c', :g, :u)"
        ),
        {"i": chatroom_id, "w": workspace_id, "g": str(uuid.uuid4()), "u": user_id},
    )
    for type_id, key, version in ((seeded.unbumped_type, "v1", 1), (seeded.bumped_type, "v3", 3)):
        conn.execute(
            sa.text(
                "INSERT INTO activity_types (id, project_id, key, name, validator_kind, version, "
                "payload_schema) VALUES (:i, :p, :k, 'n', 'in_process', :v, CAST(:s AS jsonb))"
            ),
            {"i": type_id, "p": project_id, "k": key, "v": version, "s": f'{{"title": "{key}"}}'},
        )
    for type_id, submission_id in (
        (seeded.unbumped_type, seeded.unbumped_submission),
        (seeded.bumped_type, seeded.bumped_submission),
    ):
        session_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO activity_sessions (id, activity_type_id, chatroom_id, subject_user_id) "
                "VALUES (:i, :t, :c, :u)"
            ),
            {"i": session_id, "t": type_id, "c": chatroom_id, "u": user_id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO activity_submissions "
                "(id, session_id, activity_type_id, chatroom_id, producer_user_id, attempt_no) "
                "VALUES (:i, :s, :t, :c, :u, 1)"
            ),
            {"i": submission_id, "s": session_id, "t": type_id, "c": chatroom_id, "u": user_id},
        )
    trans.commit()
    return seeded


def test_the_upgrade_seeds_revisions_and_backfills_only_what_is_provable(
    scratch_conn: sa.engine.Connection,
) -> None:
    seeded = _seed(scratch_conn)

    _run(scratch_conn, "upgrade")

    revisions = {
        (r.activity_type_id, r.version): r.payload_schema
        for r in scratch_conn.execute(
            sa.text("SELECT activity_type_id, version, payload_schema FROM activity_type_revisions")
        )
    }
    assert revisions == {
        (seeded.unbumped_type, 1): {"title": "v1"},
        (seeded.bumped_type, 3): {"title": "v3"},
    }
    versions = dict(
        scratch_conn.execute(sa.text("SELECT id, type_version FROM activity_submissions")).tuples().all()
    )
    assert versions == {seeded.unbumped_submission: 1, seeded.bumped_submission: None}


def test_the_downgrade_removes_all_of_it(scratch_conn: sa.engine.Connection) -> None:
    _seed(scratch_conn)
    _run(scratch_conn, "upgrade")
    assert _table_exists(scratch_conn, "activity_type_revisions")

    _run(scratch_conn, "downgrade")

    assert not _table_exists(scratch_conn, "activity_type_revisions")
    assert not _column_exists(scratch_conn, "activity_submissions", "type_version")
    # The data the migration did not create is untouched.
    assert scratch_conn.execute(sa.text("SELECT count(*) FROM activity_submissions")).scalar_one() == 2
