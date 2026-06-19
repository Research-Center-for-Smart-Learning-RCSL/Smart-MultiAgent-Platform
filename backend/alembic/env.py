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

from sqlalchemy import engine_from_config, pool

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


def _sync_dsn() -> str:
    """Convert the runtime async DSN to a sync one Alembic can drive."""
    dsn = get_settings().database.dsn
    if "+asyncpg" in dsn:
        return dsn.replace("+asyncpg", "+psycopg")
    return dsn


def run_migrations_offline() -> None:
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
