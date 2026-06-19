"""Arq job enqueue helper for the web process (K.3).

The arq worker enqueues jobs through the ``ArqRedis`` already in its task
``ctx``; the web process has no such handle, so it opens a short-lived pool.
This mirrors the existing per-call ``create_pool`` pattern in
``attachment_service`` / ``exports`` and centralises it so future callers do not
re-implement it.

A fresh pool per enqueue trades a little Redis connect latency for not holding a
loop-bound global pool across the request lifecycle — acceptable for the
low-frequency dispatch sites (message send, presence) this serves.
"""

from __future__ import annotations

from typing import Any

__all__ = ["enqueue"]


async def enqueue(job_name: str, *args: Any, **kwargs: Any) -> None:
    """Enqueue an arq job by name. Raises on Redis failure — callers that must
    not fail their primary operation (e.g. a user message send) should wrap this
    in a best-effort guard."""
    from arq.connections import RedisSettings, create_pool

    from app.config.settings import get_settings

    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis.dsn))
    try:
        await pool.enqueue_job(job_name, *args, **kwargs)
    finally:
        await pool.aclose(close_connection_pool=True)
