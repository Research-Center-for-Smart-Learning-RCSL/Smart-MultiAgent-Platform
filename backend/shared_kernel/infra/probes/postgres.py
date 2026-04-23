"""Postgres readiness — `SELECT 1`. Uses asyncpg via SQLAlchemy engine."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import Settings

from .base import ProbeResult


async def probe_postgres(settings: Settings) -> ProbeResult:
    engine = create_async_engine(
        settings.database.dsn,
        pool_pre_ping=False,
        pool_size=1,
        max_overflow=0,
        connect_args={"timeout": 1.5},
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            one = result.scalar_one()
            if one != 1:
                return ProbeResult("postgres", False, f"unexpected={one!r}")
    finally:
        await engine.dispose()
    return ProbeResult("postgres", True)
