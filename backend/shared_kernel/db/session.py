"""Async SQLAlchemy engine + session factory.

One engine per process; sessions are per-request. FastAPI dependencies pull
a session via `db_session()` which opens a transaction, yields, commits on
success, rolls back on error.

SoC: no context imports. Importing this module is safe from the application
layer or from tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import get_settings
from shared_kernel.observability.metrics import DB_POOL_IN_USE

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

_STATEMENT_TIMEOUT_KEY: Final = "statement_timeout"


def _build() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    settings = get_settings().database
    engine = create_async_engine(
        settings.dsn,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                _STATEMENT_TIMEOUT_KEY: str(settings.statement_timeout_ms),
            }
        },
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)

    @event.listens_for(engine.sync_engine, "checkout")
    def _on_checkout(dbapi_conn, conn_record, conn_proxy):  # noqa: ARG001
        DB_POOL_IN_USE.inc()

    @event.listens_for(engine.sync_engine, "checkin")
    def _on_checkin(dbapi_conn, conn_record):  # noqa: ARG001
        DB_POOL_IN_USE.dec()

    return engine, sm


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine, _sessionmaker = _build()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        _engine, _sessionmaker = _build()
    return _sessionmaker


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


async def db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields a session inside a transaction."""
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            yield session


@asynccontextmanager
async def async_session() -> AsyncIterator[AsyncSession]:
    """Standalone session for workers / background tasks (outside FastAPI)."""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


__all__ = ["async_session", "db_session", "dispose", "get_engine", "get_sessionmaker"]
