"""Async SQLAlchemy engine + session factory.

One engine per process; sessions are per-request. FastAPI dependencies pull
a session via `db_session()` which opens a transaction, yields, and commits
before the response is sent (rolling back on error).

SoC: no context imports. Importing this module is safe from the application
layer or from tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Final

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import HTTPConnection

from app.config.settings import get_settings
from shared_kernel.observability.metrics import (
    DB_POOL_AVAILABLE,
    DB_POOL_IN_USE,
    DB_POOL_SIZE,
)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

_STATEMENT_TIMEOUT_KEY: Final = "statement_timeout"


def _build() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    settings = get_settings().database
    engine = create_async_engine(
        settings.dsn,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout,
        pool_recycle=settings.pool_recycle,
        pool_pre_ping=True,
        connect_args={
            "timeout": settings.connect_timeout,
            "server_settings": {
                _STATEMENT_TIMEOUT_KEY: str(settings.statement_timeout_ms),
            },
        },
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)

    capacity = settings.pool_size + settings.max_overflow
    DB_POOL_SIZE.set(capacity)
    DB_POOL_AVAILABLE.set(capacity)

    @event.listens_for(engine.sync_engine, "checkout")
    def _on_checkout(dbapi_conn: Any, conn_record: Any, conn_proxy: Any) -> None:
        DB_POOL_IN_USE.inc()
        DB_POOL_AVAILABLE.dec()

    @event.listens_for(engine.sync_engine, "checkin")
    def _on_checkin(dbapi_conn: Any, conn_record: Any) -> None:
        DB_POOL_IN_USE.dec()
        DB_POOL_AVAILABLE.inc()

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


@asynccontextmanager
async def _request_transaction(session: AsyncSession) -> AsyncIterator[None]:
    """Commit on success, roll back on error. Audit tail publishes after commit
    so subscribers never see events for a rolled-back transaction."""
    from shared_kernel.audit import flush_tail_events

    try:
        yield
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    await flush_tail_events(session)


async def db_session(conn: HTTPConnection) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — yields a session; commits on success, rolls back on error.

    The dependency owns the transaction: endpoints normally do not call
    ``commit()`` themselves (see the agents/chatrooms routers). An endpoint that
    must run work *after* a durable commit — e.g. enqueueing Arq jobs that
    reference a just-written row — may call ``await session.commit()`` itself;
    the trailing commit here is then a harmless no-op on an empty transaction.

    The commit runs on FastAPI's *function* exit stack, not on the dependency's
    own teardown. Dependency teardown is registered on ``fastapi_inner_astack``,
    which FastAPI unwinds only after ``await response(scope, receive, send)`` —
    so committing there returns 201 to the client while the rows are still
    uncommitted, and any client that reads immediately can miss its own write.
    That window is not theoretical: it made an e2e seed observe a key-group
    membership it had just created as absent, and it is reachable by any API
    consumer doing read-after-write. ``fastapi_function_astack`` unwinds
    directly after the endpoint returns and before the response is sent, which
    is where a request transaction belongs.

    The trailing commit below is the safety net for the remaining window: other
    yield-dependencies tear down on the request stack, i.e. after the early
    commit, so anything they write would otherwise be dropped. It is a no-op on
    a clean session, and ``flush_tail_events`` drains its queue, so the second
    call publishes nothing.
    """
    from shared_kernel.audit import flush_tail_events

    sm = get_sessionmaker()
    async with sm() as session:
        stack = conn.scope.get("fastapi_function_astack")
        if isinstance(stack, AsyncExitStack):
            await stack.enter_async_context(_request_transaction(session))
        try:
            yield session
            await session.commit()
            await flush_tail_events(session)
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def async_session() -> AsyncIterator[AsyncSession]:
    """Standalone session for workers / background tasks (outside FastAPI)."""
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


__all__ = ["async_session", "db_session", "dispose", "get_engine", "get_sessionmaker"]
