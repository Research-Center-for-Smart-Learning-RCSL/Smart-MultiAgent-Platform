"""Redis pub/sub consumer that punches DEKs out of the router cache (D.7).

Runs as a long-lived background task in `app.workers`. One task per app
process; subscribes to both `key.revoked` and `key.carry_revoked` and
drops any matching id from `provider_router.DEK_CACHE`.

SoC: no DB, no Vault. A single Redis subscription loop with clean cancel.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from contexts.keys.application.provider_router import DEK_CACHE
from shared_kernel.auth.clients import get_redis
from shared_kernel.events.key_revocation import (
    CHANNEL_KEY_CARRY_REVOKED,
    CHANNEL_KEY_REVOKED,
)

_log = logging.getLogger(__name__)


async def run() -> None:
    """Forever-subscribe until cancelled."""
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(CHANNEL_KEY_REVOKED, CHANNEL_KEY_CARRY_REVOKED)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            channel = msg.get("channel")
            if isinstance(channel, bytes):
                channel = channel.decode()
            data = msg.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            _handle(channel, data)
    except asyncio.CancelledError:
        await pubsub.unsubscribe()
        await pubsub.aclose()  # type: ignore[no-untyped-call]
        raise


def _handle(channel: str, data: str) -> None:
    try:
        if channel == CHANNEL_KEY_REVOKED:
            DEK_CACHE.drop(uuid.UUID(data))
        elif channel == CHANNEL_KEY_CARRY_REVOKED:
            key_id, _project_id = data.split(":", 1)
            DEK_CACHE.drop(uuid.UUID(key_id))
    except (ValueError, KeyError):
        _log.warning("revocation message ignored channel=%s data=%r", channel, data)


__all__ = ["run"]
