"""Redis pub/sub consumer that punches DEKs out of the router cache (D.7).

Runs as a long-lived background task in `app.workers` and the FastAPI app
lifespan. One task per app process; subscribes to both `key.revoked` and
`key.carry_revoked` and drops any matching id from
`provider_router.DEK_CACHE`.

SoC: no DB, no Vault. A single Redis subscription loop with clean cancel.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress

from contexts.keys.application.provider_router import DEK_CACHE
from shared_kernel.auth.clients import get_redis
from shared_kernel.events.key_revocation import (
    CHANNEL_KEY_CARRY_REVOKED,
    CHANNEL_KEY_REVOKED,
)

_log = logging.getLogger(__name__)

# A dropped Redis connection (or a subscribe failure at boot) must not kill the
# listener for the process lifetime — a dead listener silently re-introduces
# ASYNC-2: revoked / carry-withdrawn DEKs keep being served from the in-process
# cache until their TTL lapses (§7.4). On any non-cancellation error we wait
# this long, then re-subscribe.
_RESTART_BACKOFF_SECONDS = 1.0


async def run() -> None:
    """Subscribe to key-revocation events until cancelled (D.7 / ASYNC-2).

    A single subscription is wrapped in a supervising loop: a dropped Redis
    connection — or any other non-cancellation error — is logged and the
    listener re-subscribes after a short backoff. Without this a single Redis
    blip would silently kill the listener for the rest of the process's life,
    and a dead listener means revoked / carry-withdrawn DEKs keep being served
    from the ``provider_router`` cache until their TTL lapses (§7.4).

    Cancellation is the clean-shutdown path and exits immediately.
    """
    while True:
        try:
            await _listen_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception(
                "key-revocation listener dropped; re-subscribing in %.1fs",
                _RESTART_BACKOFF_SECONDS,
            )
            await asyncio.sleep(_RESTART_BACKOFF_SECONDS)


async def _listen_once() -> None:
    """Hold one Redis pub/sub subscription until it ends or is cancelled.

    Raises on a dropped connection so the supervising :func:`run` loop can
    re-subscribe; re-raises ``CancelledError`` for the clean-shutdown path.
    """
    pubsub = get_redis().pubsub()
    try:
        # subscribe() is inside the try so a failure here (Redis down at boot)
        # still hits the finally cleanup — otherwise the supervising retry loop
        # would leak a pub/sub connection on every attempt while Redis is down.
        await pubsub.subscribe(CHANNEL_KEY_REVOKED, CHANNEL_KEY_CARRY_REVOKED)
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
    finally:
        # Release the channel on *every* exit path — clean cancellation and a
        # dropped connection alike — so a restart begins from a fresh
        # subscription. Best-effort: a connection that is already gone makes
        # unsubscribe/aclose raise, which must not mask the original error.
        with suppress(Exception):
            await pubsub.unsubscribe()
        with suppress(Exception):
            await pubsub.aclose()  # type: ignore[no-untyped-call]


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
