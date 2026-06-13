"""K.7 wiring tier fixtures — keep the process-global engine/Redis singletons
from leaking across pytest-asyncio's per-function event loops.

The engine (``shared_kernel.db.session``) and Redis client
(``shared_kernel.auth.clients``) are built once per process and bind their
connection pools to whatever event loop first touches them. pytest-asyncio runs
each ``async`` test on a fresh function-scoped loop, so the second wiring test
that reused those singletons would raise ``RuntimeError: Future attached to a
different loop`` (or hang on a dead connection).

This autouse fixture disposes both singletons around every test, so each test
builds pools bound to its own loop. Wiring tests are few and DB-backed, so the
per-test reconnect cost is negligible.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from shared_kernel.auth import clients
from shared_kernel.db import session as db_session


@pytest_asyncio.fixture(autouse=True)
async def _isolate_global_clients() -> AsyncIterator[None]:
    # Start clean: drop any engine/Redis bound to a previous test's loop.
    await db_session.dispose()
    await clients.close_redis()
    clients.reset_for_tests()
    try:
        yield
    finally:
        # Release this test's pools so the next loop starts fresh.
        await db_session.dispose()
        await clients.close_redis()
        clients.reset_for_tests()
