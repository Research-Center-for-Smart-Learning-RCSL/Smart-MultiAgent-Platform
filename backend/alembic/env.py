"""Alembic environment.

SoC notes:
  * This file imports **only** `app.config.settings` + `shared_kernel.db`.
    It does NOT import any `contexts.*` to keep migrations loadable without
    the full application graph.
  * The DB URL comes from a single source (Settings.database.dsn); we coerce
    the async driver suffix off so Alembic can drive the sync psycopg dialect
    (`+asyncpg` → `+psycopg`). Keeps one URL string in one place.
  * `target_metadata` is taken from `shared_kernel.db.metadata`. Later
    phases register per-context tables by importing them in `shared_kernel.db`
    side-effect-free — see that module's docstring.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import Connection, engine_from_config, pool, text

from alembic import context

# Ensure `app` + `shared_kernel` import from the backend package root when
# alembic is invoked via `python -m alembic ...` or `smap.bootstrap db-init`.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import app.db_registry as _registry  # noqa: E402, F401 — side-effect import
from app.config.settings import get_settings  # noqa: E402
from shared_kernel.db import metadata as _metadata  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = _metadata

# This tree's revision ids are the full descriptive filename stem, and two of them
# overflow the VARCHAR(32) Alembic gives `alembic_version.version_num`:
# `0032_audit_retention_delete_grant` (33) and `0040_message_attachment_extracted_text`
# (38). A fresh `upgrade head` therefore aborted on the UPDATE into 0032 with
# StringDataRightTruncation. Alembic 1.13.3 exposes no option for the width — it
# hardcodes `Column("version_num", String(32))` (`runtime/migration.py:196`) and accepts
# only version_table / _schema / _pk — so the column is widened here instead.
# `_ensure_version_table_width` runs before Alembic touches the table; Alembic adopts an
# existing table as-is rather than re-creating it, so the wider column survives.
# Keep >= the longest revision id this tree will ever carry.
_VERSION_NUM_LENGTH = 255

_VERSION_TABLE = "alembic_version"


def _ensure_version_table_width(connection: Connection) -> None:
    """Widen (or pre-create) `alembic_version.version_num` before Alembic uses it.

    Idempotent and lock-free on an already-wide column: the ALTER is issued only when
    the column is measurably too narrow, so the common case is a single catalogue read.

    Commits before returning, and that is load-bearing rather than tidiness. Any
    statement here — including the catalogue SELECT — autobegins a transaction on the
    connection, and `context.begin_transaction()` hands commit responsibility back to
    the caller when it finds one already open. Leaving it open makes Alembic skip its
    own commit, so every migration silently rolls back at connection close while still
    logging success.
    """
    # `current_schema()` keeps this to the schema Alembic itself will resolve the table
    # in; an `alembic_version` sitting in some other schema is not ours to touch.
    row = connection.execute(
        text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :t AND column_name = 'version_num'"
        ),
        {"t": _VERSION_TABLE},
    ).first()

    if row is None:
        # Fresh DB. Pre-create at full width — matching Alembic's own DDL, whose PK is
        # named `{table}_pkc` — so Alembic adopts this table instead of creating its own.
        connection.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_VERSION_TABLE} ("
                f"  version_num VARCHAR({_VERSION_NUM_LENGTH}) NOT NULL,"
                f"  CONSTRAINT {_VERSION_TABLE}_pkc PRIMARY KEY (version_num)"
                f")"
            )
        )
    elif row[0] is not None and row[0] < _VERSION_NUM_LENGTH:
        # A NULL length means an unbounded type (TEXT) — already wide enough, leave it.
        connection.execute(
            text(
                f"ALTER TABLE {_VERSION_TABLE} "
                f"ALTER COLUMN version_num TYPE VARCHAR({_VERSION_NUM_LENGTH})"
            )
        )

    connection.commit()


def _sync_dsn() -> str:
    """Convert the runtime async DSN to a sync one Alembic can drive."""
    dsn = get_settings().database.dsn
    if "+asyncpg" in dsn:
        return dsn.replace("+asyncpg", "+psycopg")
    return dsn


def run_migrations_offline() -> None:
    """Emit migrations as SQL (`--sql`).

    No connection exists here, so `_ensure_version_table_width` cannot run and a
    generated script would re-create the VARCHAR(32) version table this module works
    around. Nothing in this repo drives `--sql` (every path is online `upgrade head`),
    so rather than half-fix it: an operator generating a script for a fresh database
    must widen `alembic_version.version_num` themselves before applying it.
    """
    context.configure(
        url=_sync_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    ini_section = config.get_section(config.config_ini_section) or {}
    ini_section["sqlalchemy.url"] = _sync_dsn()
    connectable = engine_from_config(
        ini_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _ensure_version_table_width(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
