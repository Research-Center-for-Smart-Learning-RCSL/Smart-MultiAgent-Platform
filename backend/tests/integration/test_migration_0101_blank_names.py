"""Migration 0101 clears stored names that have no visible character (AC-5).

Same scratch-database harness as ``test_migration_0100_display_names``: these run
real migrations, so they need a throwaway database named by
``SMAP_SCRATCH_DATABASE_URL`` and skip without one, and the DSN redirect is
verified rather than assumed because getting it wrong is destructive.

    SMAP_SCRATCH_DATABASE_URL=postgresql+asyncpg://smap:smap@localhost:5432/smap_scratch \\
        pytest tests/integration/test_migration_0101_blank_names.py -m db
"""

from __future__ import annotations

import hashlib
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

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0101_blank_display_names.py"
_spec = importlib.util.spec_from_file_location("_migration_0101_blank_names", _MIGRATION_PATH)
assert _spec is not None
assert _spec.loader is not None
migration_0101 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_0101)

_SCRATCH_URL = os.environ.get("SMAP_SCRATCH_DATABASE_URL")

_FILLER = chr(0x3164)  # HANGUL FILLER
_BLANK_CELL = chr(0x2800)  # BRAILLE PATTERN BLANK
_ACUTE = chr(0x0301)
_ZWJ = chr(0x200D)

_BLANK = {"filler": _FILLER, "braille": _BLANK_CELL, "marks": _ACUTE * 3, "joiner": _ZWJ}
# "cjk" and "heart" have no ASCII letter or digit, so they pass the SQL prefilter
# and reach the Python predicate, which must keep them.
_KEPT = {
    "clean": "Carol Chen",
    "inner_filler": f"Al{_FILLER}ice",
    "accented": "e" + _ACUTE,
    "cjk": "柯老師",
    "heart": chr(0x2764) + chr(0xFE0F),
}
_SEED = {**_BLANK, **_KEPT}


@dataclass
class _Seeded:
    users: dict[str, uuid.UUID]
    room: uuid.UUID
    sessions: dict[str, uuid.UUID]
    bans: dict[str, uuid.UUID]


@pytest.fixture
def scratch_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.engine.Connection]:
    """A connection to a throwaway database, migrated to the revision before 0101."""
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
            command.upgrade(cfg, migration_0101.down_revision)
            with engine.connect() as conn:
                yield conn
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()


def _seed(conn: sa.engine.Connection) -> _Seeded:
    users = {key: uuid.uuid4() for key in _SEED}
    owner, project, workspace, room = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    sessions = {key: uuid.uuid4() for key in _SEED}
    bans = {key: uuid.uuid4() for key in _SEED}
    with conn.begin():
        conn.execute(
            sa.text("INSERT INTO users (id, email, password_hash) VALUES (:i, 'owner@example.com', 'x')"),
            {"i": owner},
        )
        conn.execute(
            sa.text(
                "INSERT INTO projects (id, name, owner_user_id, created_by_user_id) VALUES (:i, 'p', :u, :u)"
            ),
            {"i": project, "u": owner},
        )
        conn.execute(
            sa.text("INSERT INTO workspaces (id, project_id, name) VALUES (:i, :p, 'w')"),
            {"i": workspace, "p": project},
        )
        conn.execute(
            sa.text(
                "INSERT INTO chatrooms (id, workspace_id, name, guest_token, created_by_user_id) "
                "VALUES (:i, :w, 'c', :g, :u)"
            ),
            {"i": room, "w": workspace, "g": uuid.uuid4().hex, "u": owner},
        )
        for key, name in _SEED.items():
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, password_hash, display_name) VALUES (:i, :e, 'x', :n)"
                ),
                {"i": users[key], "e": f"{key}@example.com", "n": name},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO chatroom_guests (chatroom_id, user_id, joined_via_token, display_name) "
                    "VALUES (:r, :u, 't', :n)"
                ),
                {"r": room, "u": users[key], "n": name},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO guest_sessions (id, chatroom_id, display_name, refresh_token_hash) "
                    "VALUES (:i, :r, :n, :h)"
                ),
                {"i": sessions[key], "r": room, "n": name, "h": hashlib.sha256(key.encode()).hexdigest()},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO chatroom_guest_bans (id, chatroom_id, guest_session_id, display_name) "
                    "VALUES (:i, :r, :s, :n)"
                ),
                {"i": bans[key], "r": room, "s": sessions[key], "n": name},
            )
    return _Seeded(users=users, room=room, sessions=sessions, bans=bans)


def _read(conn: sa.engine.Connection, sql: str, ids: dict[str, uuid.UUID]) -> dict[str, str | None]:
    by_id = {row.id: row.display_name for row in conn.execute(sa.text(sql)).all()}
    conn.rollback()
    return {key: by_id[uid] for key, uid in ids.items()}


def _state(conn: sa.engine.Connection, seeded: _Seeded) -> dict[str, dict[str, str | None]]:
    return {
        "users": _read(conn, "SELECT id, display_name FROM users", seeded.users),
        "guests": _read(conn, "SELECT user_id AS id, display_name FROM chatroom_guests", seeded.users),
        "sessions": _read(conn, "SELECT id, display_name FROM guest_sessions", seeded.sessions),
        "bans": _read(conn, "SELECT id, display_name FROM chatroom_guest_bans", seeded.bans),
    }


def _run(conn: sa.engine.Connection, step: str) -> None:
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx), conn.begin():
        getattr(migration_0101, step)()


def _expected() -> dict[str, dict[str, str | None]]:
    def repaired(blank: str | None) -> dict[str, str | None]:
        return {key: (blank if key in _BLANK else name) for key, name in _SEED.items()}

    return {
        "users": repaired(None),
        "guests": repaired(None),
        "sessions": repaired("Guest"),
        "bans": repaired("Guest"),
    }


def test_upgrade_clears_blank_names_and_leaves_the_rest(scratch_conn: sa.engine.Connection) -> None:
    seeded = _seed(scratch_conn)

    _run(scratch_conn, "upgrade")

    assert _state(scratch_conn, seeded) == _expected()


def test_upgrade_is_idempotent(scratch_conn: sa.engine.Connection) -> None:
    seeded = _seed(scratch_conn)
    _run(scratch_conn, "upgrade")
    first = _state(scratch_conn, seeded)

    _run(scratch_conn, "upgrade")

    assert _state(scratch_conn, seeded) == first


def test_downgrade_is_a_clean_no_op(scratch_conn: sa.engine.Connection) -> None:
    seeded = _seed(scratch_conn)
    _run(scratch_conn, "upgrade")
    repaired = _state(scratch_conn, seeded)

    _run(scratch_conn, "downgrade")

    assert _state(scratch_conn, seeded) == repaired


def test_upgrade_walks_every_page(
    scratch_conn: sa.engine.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows are read in key-ordered pages, chatroom_guests by its composite key; a
    page smaller than the seed must still reach every row past the first boundary."""
    seeded = _seed(scratch_conn)
    monkeypatch.setattr(migration_0101, "_PAGE", 1)

    _run(scratch_conn, "upgrade")

    assert _state(scratch_conn, seeded) == _expected()
