"""Periodic cleanup for anonymous guest sessions (AC-10).

Purges guest_sessions rows where last_seen_at is older than 30 days.
Registered as an Arq cron in the main worker.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from loguru import logger

from shared_kernel.auth.clients import now
from shared_kernel.db.session import get_sessionmaker

_CLEANUP_WINDOW = timedelta(days=30)


async def guest_session_cleanup(ctx: dict[str, Any]) -> int:
    from contexts.conversation.infrastructure.repositories import GuestSessionRepository

    cutoff = now() - _CLEANUP_WINDOW
    sm = get_sessionmaker()
    async with sm() as session, session.begin():
        repo = GuestSessionRepository(session)
        deleted = await repo.delete_older_than(cutoff)

    logger.bind(event="guest_session_cleanup", deleted=deleted).info(
        "purged %d guest sessions older than %s", deleted, cutoff.isoformat()
    )
    return deleted
