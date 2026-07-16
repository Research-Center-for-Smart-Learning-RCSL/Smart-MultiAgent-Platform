"""Bounded Qdrant client lifecycle for the configless collection teardown (F-3).

SoC: the application services decide *whether* a project's collection may be torn
down (the pin + live-config invariant); this module owns *how* the call is made --
client construction, the timeout bound, and closing the client. It deliberately does
not catch Qdrant errors: the caller maps a raise to ``TeardownOutcome.FAILED`` and
answers it by retaining the embedding pin, so swallowing here would recreate the very
bug F-3 records.

The bound matters because the teardown runs inside an open Postgres transaction
holding the ``(project, kind)`` advisory lock. An unbounded call would hold a pooled
connection and block config creation for that project for as long as Qdrant hangs.

The ceiling covers the delete itself. It does not cover ``close``, which is left
outside so that cleanup runs after the timeout has already been converted to
``TimeoutError`` rather than while cancellation is still in flight -- so a Qdrant that
hangs on connection teardown can still extend the lock hold past the timeout. Bounding
that too would mean a second ceiling whose own expiry has no meaningful handling.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from qdrant_client import AsyncQdrantClient


async def delete_collection_bounded(op: Callable[[AsyncQdrantClient], Awaitable[bool]]) -> bool:
    """Run ``op`` against a fresh client, bounded by ``qdrant.teardown_timeout_s``.

    Returns what ``op`` returns: ``True`` when a collection was actually dropped,
    ``False`` when it was already absent. Both confirm the collection is gone. Raises
    on any Qdrant error or on timeout.
    """
    from app.config.settings import get_settings

    settings = get_settings()
    client = AsyncQdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None)
    try:
        # The ceiling covers `op` only, so that `close` below runs after the timeout
        # has already been converted to TimeoutError rather than while cancellation is
        # still propagating. Constructing the client does no I/O, so it needs no bound.
        async with asyncio.timeout(settings.qdrant.teardown_timeout_s):
            return await op(client)
    finally:
        await client.close()


__all__ = ["delete_collection_bounded"]
