"""`smap.bootstrap db-init` — extensions + Alembic baseline stamp.

Extensions (`pgvector`, `pg_cron`, `pgcrypto`, `uuid-ossp`) are created here
idempotently so a fresh compose stack or a scratch CI DB lines up with §25
regardless of whether the postgres init SQL ran. Alembic is then advanced to
`head` (no-op on a fresh DB because 0000_baseline is empty).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from app.config.settings import Settings

from ._common import BootstrapReport

_EXTENSIONS = ("pgcrypto", "uuid-ossp", "vector", "pg_cron")


def _sync_dsn(settings: Settings) -> str:
    dsn = settings.database.dsn
    return dsn.replace("+asyncpg", "+psycopg") if "+asyncpg" in dsn else dsn


def _alembic_config() -> AlembicConfig:
    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    return AlembicConfig(str(ini))


def run(settings: Settings) -> BootstrapReport:
    report = BootstrapReport(subcommand="db-init")
    engine = create_engine(_sync_dsn(settings), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            existing = {row[0] for row in conn.execute(text("SELECT extname FROM pg_extension"))}
            for ext in _EXTENSIONS:
                if ext in existing:
                    report.already(f"extension:{ext}")
                    continue
                try:
                    conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{ext}"'))
                    report.did(f"extension:{ext}")
                except Exception as exc:  # — pg_cron needs shared_preload_libraries
                    report.skipped(f"extension:{ext}", str(exc).splitlines()[0])
    finally:
        engine.dispose()

    alembic_cfg = _alembic_config()

    # Resolve the head revision so we can report idempotently.
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    head_rev = script.get_current_head()

    check_engine = create_engine(_sync_dsn(settings), isolation_level="AUTOCOMMIT")
    try:
        with check_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()
    finally:
        check_engine.dispose()

    if current_rev == head_rev:
        report.already("alembic:upgrade", head_rev or "head")
    else:
        alembic_command.upgrade(alembic_cfg, "head")
        report.did("alembic:upgrade", "head")

    return report
