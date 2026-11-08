"""Migration 0109's judge columns, constraint and index, in both directions.

Spec: docs/tasks/2026-11-08-research-data-integrity/spec.md, section 5
(Creativity scoring).

Same harness as ``test_migration_0108_type_revisions``: a throwaway database
named by ``SMAP_SCRATCH_DATABASE_URL``, skipped when absent, with the DSN
redirect verified before any DDL runs.
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

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0109_creativity_judging.py"
_spec = importlib.util.spec_from_file_location("_migration_0109", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0109 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0109)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")

_SUBMISSION_COLUMNS = ("judge_status", "judge_scores", "judge_reason", "judged_at")


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to revision 0108."""
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
            command.upgrade(cfg, "0108_activity_type_revisions")
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _run(conn: sa.engine.Connection, step: str) -> None:
    conn.rollback()
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx), conn.begin():
        getattr(migration_0109, step)()


def _columns(conn: sa.engine.Connection, table: str) -> set[str]:
    return set(
        conn.execute(
            sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        ).scalars()
    )


def _has_constraint(conn: sa.engine.Connection, name: str) -> bool:
    return bool(conn.execute(sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}).first())


def _has_index(conn: sa.engine.Connection, name: str) -> bool:
    return bool(conn.execute(sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}).first())


def test_the_upgrade_adds_the_columns_the_check_and_the_index(scratch_conn: sa.engine.Connection) -> None:
    _run(scratch_conn, "upgrade")

    assert "creativity_scoring" in _columns(scratch_conn, "activity_types")
    assert "creativity_scoring" in _columns(scratch_conn, "activity_type_revisions")
    assert set(_SUBMISSION_COLUMNS) <= _columns(scratch_conn, "activity_submissions")
    assert _has_constraint(scratch_conn, "ck_activity_submissions_judge_status")
    assert _has_index(scratch_conn, "ix_activity_submissions_judge_pending")
    definition = scratch_conn.execute(
        sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
        {"n": "ck_activity_submissions_judge_status"},
    ).scalar_one()
    for status in ("pending", "scored", "skipped", "error"):
        assert status in definition


def test_the_downgrade_removes_all_of_it(scratch_conn: sa.engine.Connection) -> None:
    _run(scratch_conn, "upgrade")
    _run(scratch_conn, "downgrade")

    assert "creativity_scoring" not in _columns(scratch_conn, "activity_types")
    assert "creativity_scoring" not in _columns(scratch_conn, "activity_type_revisions")
    assert not set(_SUBMISSION_COLUMNS) & _columns(scratch_conn, "activity_submissions")
    assert not _has_constraint(scratch_conn, "ck_activity_submissions_judge_status")
    assert not _has_index(scratch_conn, "ix_activity_submissions_judge_pending")
