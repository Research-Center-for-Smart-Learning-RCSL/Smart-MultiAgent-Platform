"""Per-WS connection loop: bounded queue + backpressure + in-socket refresh.

Every `/ws/*` endpoint is structurally identical once auth and subscription
are resolved:

  - open socket
  - subscribe to one or more Redis channels
  - fan out published events to the client
  - accept inbound control messages (`refresh`, `ping`)
  - disconnect on slow consumer (queue overflow) or token failure

This module owns that loop so the endpoints stay tiny. The per-user cap
(R19.03) is enforced here too — consolidating connection lifecycle in one
file means that "register / unregister with the Redis cap" can't be
forgotten when a new WS endpoint is added.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger
from redis.exceptions import ResponseError

from app.config.settings import get_settings
from shared_kernel.auth.clients import get_redis
from shared_kernel.auth.permissions import Principal
from shared_kernel.observability.metrics import (
    WS_CONNECTIONS_ACTIVE,
    WS_PER_USER_REJECTIONS,
)
from shared_kernel.realtime.pubsub import Subscriber
from shared_kernel.realtime.ws_auth import WsAuthError, refresh_principal

_OUTBOUND_QUEUE_MAX = 256  # bounded per connection
_CLOSE_POLICY_VIOLATION = 1008
_CLOSE_TRY_AGAIN_LATER = 1013
_CLOSE_AUTH_FAILED = 4401  # app-level code, see §22.14
# ASYNC-7: a live client sends periodic `ping` frames (and presence
# heartbeats); silence past this window means the socket is half-open (the
# client vanished without a TCP FIN). The server reaps it so the connection
# slot and the per-user Redis cap entry are released. Generous enough — two-plus
# missed client heartbeats (60 s TTL) — not to reap a merely-quiet client.
_IDLE_TIMEOUT_SECONDS = 120.0
# ASYNC-7: a connection refreshes its heartbeat score in the per-user cap ZSET
# on every inbound frame. A score older than this — deliberately longer than
# the idle timeout, so a live connection is always fresh — means the owning
# process crashed without cleanup; such entries are pruned so a hard crash
# cannot permanently consume the cap.
_CONN_STALE_SECONDS = 300


def _user_connections_key(user_id: uuid.UUID) -> str:
    return f"ws:conns:{user_id}"


@dataclass
class ChannelConnection:
    """Mutable per-connection state — owned by a single `connection_loop`."""

    ws: WebSocket
    principal: Principal
    connection_id: uuid.UUID = field(default_factory=uuid.uuid4)
    outbound: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_OUTBOUND_QUEUE_MAX),
    )

    async def enqueue(self, event: dict[str, Any]) -> bool:
        """Try to enqueue without blocking. Returns False when full —
        caller's contract is to close the socket in that case."""
        try:
            self.outbound.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False


async def _register_user_connection(
    user_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> bool:
    """Enforce ws_concurrent_per_user (R19.03). Returns False if the cap
    is already reached (caller closes with `ws-per-user-limit` problem).

    ASYNC-7: the per-user registry is a ZSET scored by each connection's last
    heartbeat. Entries from a connection whose process hard-crashed (so
    `_cleanup` never ran) go stale and are pruned by score here, on every open,
    so a crash cannot permanently consume the cap.
    """
    cap = get_settings().limits.ws_concurrent_per_user
    r = get_redis()
    key = _user_connections_key(user_id)
    now = time.time()
    try:
        # Drop connections whose heartbeat lapsed (crashed without _cleanup).
        await r.zremrangebyscore(key, "-inf", now - _CONN_STALE_SECONDS)
    except ResponseError:
        # A pre-ASYNC-7 deployment stored this key as a plain SET. Such a key is
        # by definition the un-expiring registry this fix replaces — drop it and
        # start the ZSET clean.
        await r.delete(key)
    # Not perfectly race-free under concurrent opens from the same user, but
    # bounded by a small margin (O(parallel opens)) — acceptable for a UX cap.
    await r.zadd(key, {str(connection_id): now})
    count = await r.zcard(key)
    # Backstop: if every connection for this user disappears the key self-expires.
    await r.expire(key, _CONN_STALE_SECONDS)
    if count > cap:
        await r.zrem(key, str(connection_id))
        return False
    return True


async def _touch_user_connection(
    user_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> None:
    """Refresh this connection's heartbeat score in the per-user cap ZSET (ASYNC-7)."""
    r = get_redis()
    key = _user_connections_key(user_id)
    await r.zadd(key, {str(connection_id): time.time()})
    await r.expire(key, _CONN_STALE_SECONDS)


async def _unregister_user_connection(
    user_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> None:
    await get_redis().zrem(_user_connections_key(user_id), str(connection_id))


async def connection_loop(
    *,
    ws: WebSocket,
    principal: Principal,
    subprotocol: str,
    channels: Sequence[str],
    on_open: Callable[[ChannelConnection], Awaitable[None]] | None = None,
    on_close: Callable[[ChannelConnection], Awaitable[None]] | None = None,
    on_client_message: (Callable[[ChannelConnection, dict[str, Any]], Awaitable[None]] | None) = None,
) -> None:
    """Drive a single WS connection until it closes.

    `channels` is the pub/sub list to subscribe to. Endpoint-specific setup
    (presence.join / capacity publish / etc.) goes in `on_open`; symmetric
    teardown in `on_close`. Inbound messages the connection-layer doesn't
    own (refresh, ping) are handled here; everything else falls through to
    `on_client_message` if the endpoint opted in.
    """
    conn = ChannelConnection(ws=ws, principal=principal)

    # Per-user cap — check before accepting so the accept+close path is rare.
    if not await _register_user_connection(principal.user_id, conn.connection_id):
        WS_PER_USER_REJECTIONS.inc()
        # Must accept before sending a close frame; HTTP-upgrade is already
        # complete by this point so we cannot reject at the transport level.
        await ws.accept(subprotocol=subprotocol)
        await ws.close(code=_CLOSE_TRY_AGAIN_LATER, reason="per-user WS cap reached")
        return

    try:
        await ws.accept(subprotocol=subprotocol)
    except Exception:  # pragma: no cover
        await _unregister_user_connection(principal.user_id, conn.connection_id)
        raise

    WS_CONNECTIONS_ACTIVE.inc()

    if on_open is not None:
        try:
            await on_open(conn)
        except Exception:
            logger.bind(event="ws_open_error").exception("on_open failed")
            await _cleanup(conn, on_close)
            await ws.close(code=_CLOSE_POLICY_VIOLATION, reason="open failed")
            return

    async def _reader() -> None:
        while True:
            # ASYNC-7: bound the receive so a client that vanished without a
            # TCP FIN cannot block this task forever, leaking the connection
            # slot and the per-user Redis cap entry.
            try:
                raw = await asyncio.wait_for(
                    ws.receive_text(),
                    timeout=_IDLE_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.bind(
                    event="ws_idle_timeout",
                    connection_id=str(conn.connection_id),
                ).info("ws idle timeout — closing half-open socket")
                await ws.close(
                    code=_CLOSE_TRY_AGAIN_LATER,
                    reason="idle timeout",
                )
                return
            # ASYNC-7: an inbound frame proves the socket is alive — refresh the
            # connection's heartbeat score in the per-user cap registry.
            await _touch_user_connection(conn.principal.user_id, conn.connection_id)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "ping":
                await conn.enqueue({"type": "pong"})
                continue
            if mtype == "refresh":
                token = msg.get("access_token", "")
                try:
                    new_principal = await refresh_principal(token)
                except WsAuthError:
                    await ws.close(code=_CLOSE_AUTH_FAILED, reason="refresh failed")
                    return
                # Principal must remain the same user — clients cannot hop
                # identities mid-socket.
                if new_principal.user_id != conn.principal.user_id:
                    await ws.close(code=_CLOSE_AUTH_FAILED, reason="principal changed")
                    return
                conn.principal = new_principal
                await conn.enqueue({"type": "refresh.ack"})
                continue
            if on_client_message is not None:
                await on_client_message(conn, msg)

    async def _pubsub_fanin() -> None:
        async with Subscriber(list(channels)) as sub:
            async for event in sub.events():
                if not await conn.enqueue(event):
                    # Slow consumer — drop the connection rather than block
                    # the Redis pubsub reader for other subscribers.
                    await ws.close(
                        code=_CLOSE_TRY_AGAIN_LATER,
                        reason="slow consumer",
                    )
                    return

    async def _writer() -> None:
        while True:
            event = await conn.outbound.get()
            await ws.send_text(
                json.dumps(event, default=str, separators=(",", ":")),
            )

    tasks = [
        asyncio.create_task(_reader(), name=f"ws-reader-{conn.connection_id}"),
        asyncio.create_task(_writer(), name=f"ws-writer-{conn.connection_id}"),
    ]
    if channels:
        tasks.append(
            asyncio.create_task(
                _pubsub_fanin(),
                name=f"ws-fanin-{conn.connection_id}",
            ),
        )

    try:
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.bind(event="ws_task_error").exception(
                    "ws task failed",
                    exc_info=exc,
                )
    finally:
        await _cleanup(conn, on_close)


async def _cleanup(
    conn: ChannelConnection,
    on_close: Callable[[ChannelConnection], Awaitable[None]] | None,
) -> None:
    WS_CONNECTIONS_ACTIVE.dec()
    if on_close is not None:
        try:
            await on_close(conn)
        except Exception:  # pragma: no cover
            logger.bind(event="ws_close_error").exception("on_close failed")
    await _unregister_user_connection(conn.principal.user_id, conn.connection_id)


__all__ = ["ChannelConnection", "connection_loop"]
