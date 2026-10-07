"""Migration 0100 repairs stored account display names (AC-5).

Same scratch-database harness as ``test_migration_0087_schema``: these run real
migrations, so they need a throwaway database named by
``SMAP_SCRATCH_DATABASE_URL`` and skip without one, and the DSN redirect is
verified rather than assumed because getting it wrong is destructive.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0100_display_names.py -m db
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
from shared_kernel.labels import MAX_DISPLAY_NAME, normalise_label

pytestmark = pytest.mark.db

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0100_normalise_account_display_names.py"
)
_spec = importlib.util.spec_from_file_location("_migration_0100_display_names", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0100 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0100)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")

_RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE; built with chr, ruff's PLE2502 refuses it literally
_SEED = {
    "bidi": f"Alice{_RLO}eciwlA",
    "newline": "Bob\nTeacher",
    "controls_only": "\n\t",
    "clean": "Carol Chen",
}


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to the revision before 0100."""
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
            command.upgrade(cfg, migration_0100.down_revision)
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _seed(conn: sa.engine.Connection) -> dict[str, uuid.UUID]:
    ids = {key: uuid.uuid4() for key in _SEED}
    with conn.begin():
        for key, name in _SEED.items():
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, password_hash, display_name) VALUES (:i, :e, 'x', :n)"
                ),
                {"i": ids[key], "e": f"{key}@example.com", "n": name},
            )
    return ids


def _names(conn: sa.engine.Connection, ids: dict[str, uuid.UUID]) -> dict[str, str | None]:
    rows = conn.execute(sa.text("SELECT id, display_name FROM users")).all()
    by_id = {row.id: row.display_name for row in rows}
    conn.rollback()
    return {key: by_id[uid] for key, uid in ids.items()}


def _run(conn: sa.engine.Connection, step: str) -> None:
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx), conn.begin():
        getattr(migration_0100, step)()


def test_upgrade_repairs_unsafe_names_and_leaves_clean_ones(scratch_conn: sa.engine.Connection) -> None:
    ids = _seed(scratch_conn)

    _run(scratch_conn, "upgrade")

    assert _names(scratch_conn, ids) == {
        "bidi": "AliceeciwlA",
        "newline": "BobTeacher",
        "controls_only": None,
        "clean": "Carol Chen",
    }


def test_upgrade_is_idempotent(scratch_conn: sa.engine.Connection) -> None:
    ids = _seed(scratch_conn)
    _run(scratch_conn, "upgrade")
    first = _names(scratch_conn, ids)

    _run(scratch_conn, "upgrade")

    assert _names(scratch_conn, ids) == first


def test_every_name_equals_its_own_normalisation_afterwards(scratch_conn: sa.engine.Connection) -> None:
    """AC-5, against the live rule: the migration's frozen copy agrees with it today."""
    ids = _seed(scratch_conn)

    _run(scratch_conn, "upgrade")

    for name in _names(scratch_conn, ids).values():
        if name is not None:
            assert normalise_label(name, max_len=MAX_DISPLAY_NAME) == name


def test_downgrade_is_a_clean_no_op(scratch_conn: sa.engine.Connection) -> None:
    ids = _seed(scratch_conn)
    _run(scratch_conn, "upgrade")
    repaired = _names(scratch_conn, ids)

    _run(scratch_conn, "downgrade")

    assert _names(scratch_conn, ids) == repaired


def test_the_frozen_rule_matches_the_live_one() -> None:
    """Runs without a database: the copy was taken from `normalise_label` at this
    revision, and the two must agree on the inputs the dossier names."""
    family = "\U0001f468‍\U0001f469‍\U0001f467"
    samples = [*_SEED.values(), "W" * 49 + family, "x" * 60, "  padded  ", "café" * 13]

    for raw in samples:
        assert migration_0100._normalise(raw) == normalise_label(raw, max_len=MAX_DISPLAY_NAME)
