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
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from loguru import logger
from redis.exceptions import ResponseError

from app.config.settings import get_settings
from shared_kernel.auth import tokens
from shared_kernel.auth.clients import get_redis, now
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
# API-7: inbound frames here are tiny control messages (ping / refresh /
# typing). Cap the decoded text so a hostile client cannot push a giant frame
# through json.loads — the WS path bypasses the HTTP body-size middleware. The
# largest legitimate frame is a `refresh` carrying an access token (a few KB),
# so 64 KiB is generous headroom.
_MAX_FRAME_BYTES = 64 * 1024
# SEC-H2: the handshake authorizes the token once; after `accept` the only
# re-auth was a *client-initiated* `refresh` frame, so a revoked/expired
# principal kept receiving events until it chose to disconnect. The auth
# watchdog re-checks the held token's expiry (locally) and its jti denylist
# (Redis) on this cadence, so logout / ban / session-kill tears the socket
# down within roughly one access-TTL + this interval — matching the
# per-request guarantee the HTTP middleware upholds. 30 s is well under the
# access-token TTL while keeping the Redis EXISTS probe rate negligible.
_AUTH_RECHECK_SECONDS = 30.0
# SEC-H2: the handshake authorizes room/channel access once; without this a user
# removed from a room (guest link revoked, project membership lost, ACL tightened)
# kept receiving the room's events until their access token expired. The watchdog
# re-runs the caller-supplied `authorize` probe on this cadence (a multiple of the
# auth re-check tick) and tears the socket down on access loss, matching the
# per-request ACL guarantee the HTTP path upholds.
_ROOM_REAUTH_EVERY_N_TICKS = 2  # ~60s at the 30s watchdog tick
_CLOSE_FORBIDDEN = 4403  # app-level code — access revoked mid-socket (see §22.14)
# ASYNC-7: a connection refreshes its heartbeat score in the per-user cap ZSET
# on every inbound frame. A score older than this — deliberately longer than
# the idle timeout, so a live connection is always fresh — means the owning
# process crashed without cleanup; such entries are pruned so a hard crash
# cannot permanently consume the cap.
_CONN_STALE_SECONDS = 300
# ASYNC-13: on teardown the writer is given this long to observe `shutdown` and
# return between frames before it is force-cancelled — bounding the wait so a
# writer wedged on a half-open socket cannot stall connection teardown.
_WRITER_DRAIN_GRACE_SECONDS = 2.0


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
    # SEC-H2: the access token's expiry + jti, tracked so the auth watchdog can
    # tear the socket down on expiry/revocation. Refreshed in-place when the
    # client sends a `refresh` frame. `None` means the caller opted out of
    # live re-auth (e.g. a test harness with no token metadata).
    token_expires_at: datetime | None = None
    token_jti: uuid.UUID | None = None

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
    token_expires_at: datetime | None = None,
    token_jti: uuid.UUID | None = None,
    on_open: Callable[[ChannelConnection], Awaitable[None]] | None = None,
    on_close: Callable[[ChannelConnection], Awaitable[None]] | None = None,
    on_client_message: (Callable[[ChannelConnection, dict[str, Any]], Awaitable[None]] | None) = None,
    on_heartbeat: Callable[[ChannelConnection], Awaitable[None]] | None = None,
    authorize: Callable[[ChannelConnection], Awaitable[bool]] | None = None,
) -> None:
    """Drive a single WS connection until it closes.

    `channels` is the pub/sub list to subscribe to. Endpoint-specific setup
    (presence.join / capacity publish / etc.) goes in `on_open`; symmetric
    teardown in `on_close`. Inbound messages the connection-layer doesn't
    own (refresh, ping) are handled here; everything else falls through to
    `on_client_message` if the endpoint opted in.

    `on_heartbeat` (optional) runs on every inbound frame — used to refresh
    out-of-band liveness state such as room presence. `authorize` (optional) is
    re-run periodically by the auth watchdog so an endpoint whose access can be
    revoked mid-socket (room ACL) tears the connection down on access loss; it
    returns False to deny. Both are kept here (not imported from a context) so
    `shared_kernel` stays free of context dependencies.
    """
    conn = ChannelConnection(
        ws=ws,
        principal=principal,
        token_expires_at=token_expires_at,
        token_jti=token_jti,
    )

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

    # ASYNC-13: the driver tasks never call ws.close() themselves — a close
    # racing the writer's in-flight send_text surfaced noisy exceptions. A task
    # that wants the connection gone records the intended close frame via
    # `_request_close` and returns; connection_loop performs the single,
    # authoritative ws.close() in its finally-block, once every task has
    # stopped touching the socket. `shutdown` lets the writer wind down between
    # frames so it is not torn down mid-send.
    close_code = 1000
    close_reason = ""
    shutdown = asyncio.Event()

    def _request_close(code: int, reason: str) -> None:
        nonlocal close_code, close_reason
        close_code = code
        close_reason = reason

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
                _request_close(_CLOSE_TRY_AGAIN_LATER, "idle timeout")
                return
            # API-7: refuse oversized frames before parsing. A well-behaved
            # client never approaches this; an abusive one gets the socket torn
            # down rather than a free pass to the JSON parser. Char count bounds
            # byte count (UTF-8 is 1-4 bytes/char): len > cap is always too big,
            # len*4 <= cap is always small enough, so only the band between the
            # two needs the actual encode — control frames skip it entirely.
            if len(raw) > _MAX_FRAME_BYTES or (
                len(raw) * 4 > _MAX_FRAME_BYTES and len(raw.encode("utf-8")) > _MAX_FRAME_BYTES
            ):
                logger.bind(
                    event="ws_frame_too_large",
                    connection_id=str(conn.connection_id),
                ).warning("ws frame exceeds size cap — closing")
                _request_close(_CLOSE_POLICY_VIOLATION, "frame too large")
                return
            # ASYNC-7: an inbound frame proves the socket is alive — refresh the
            # connection's heartbeat score in the per-user cap registry.
            await _touch_user_connection(conn.principal.user_id, conn.connection_id)
            if on_heartbeat is not None:
                # Out-of-band liveness (presence) — a Redis blip here must not
                # kill the reader; the next frame retries.
                with suppress(Exception):
                    await on_heartbeat(conn)
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
                    refreshed = await refresh_principal(token)
                except WsAuthError:
                    _request_close(_CLOSE_AUTH_FAILED, "refresh failed")
                    return
                # Principal must remain the same user — clients cannot hop
                # identities mid-socket.
                if refreshed.principal.user_id != conn.principal.user_id:
                    _request_close(_CLOSE_AUTH_FAILED, "principal changed")
                    return
                conn.principal = refreshed.principal
                # SEC-H2: track the *refreshed* token so the watchdog enforces
                # the new expiry/jti, not the one presented at handshake.
                conn.token_expires_at = refreshed.expires_at
                conn.token_jti = refreshed.jti
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
                    _request_close(_CLOSE_TRY_AGAIN_LATER, "slow consumer")
                    return

    async def _writer() -> None:
        # ASYNC-13: between frames, race the outbound queue against `shutdown`
        # so a peer task asking the connection to close stops the writer
        # cleanly — it returns rather than being cancelled mid-send_text,
        # leaving connection_loop's finally-block as the sole ws.close() caller.
        stopper = asyncio.ensure_future(shutdown.wait())
        try:
            while not shutdown.is_set():
                getter = asyncio.ensure_future(conn.outbound.get())
                try:
                    await asyncio.wait(
                        {getter, stopper},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    # Abandon the get if it lost the race — never leaves an
                    # item half-dequeued (asyncio.Queue.get is atomic).
                    if not getter.done():
                        getter.cancel()
                if shutdown.is_set():
                    return  # `shutdown` won (or fired during a send) — exit.
                await ws.send_text(
                    json.dumps(getter.result(), default=str, separators=(",", ":")),
                )
        finally:
            stopper.cancel()

    async def _auth_watchdog() -> None:
        # SEC-H2: re-authorize a live socket on a fixed cadence so a token that
        # has since expired or been revoked (logout / ban / session kill) tears
        # the connection down — the handshake check alone left a window the
        # full access-TTL wide. Reads `conn.token_*` each tick so an in-socket
        # refresh is honoured. This task never writes to the socket; it only
        # records the intended close frame and returns, leaving the single
        # authoritative ws.close() to connection_loop's finally-block.
        ticks = 0
        while True:
            await asyncio.sleep(_AUTH_RECHECK_SECONDS)
            ticks += 1
            if conn.token_expires_at is not None and now() >= conn.token_expires_at:
                _request_close(_CLOSE_AUTH_FAILED, "token expired")
                return
            # SEC-H2: re-check room/channel access on a coarser cadence than the
            # token probe, so a mid-socket ACL change (membership loss, guest
            # revoke) tears the connection down. A transient error fails open for
            # this window and retries — same posture as the denylist probe.
            if authorize is not None and ticks % _ROOM_REAUTH_EVERY_N_TICKS == 0:
                try:
                    allowed = await authorize(conn)
                except Exception:
                    logger.bind(
                        event="ws_reauth_check_error",
                        connection_id=str(conn.connection_id),
                    ).warning("ws room re-auth failed; retrying next window")
                else:
                    if not allowed:
                        _request_close(_CLOSE_FORBIDDEN, "room access revoked")
                        return
            jti = conn.token_jti
            if jti is None:
                continue
            try:
                denied = await tokens.is_denied(jti)
            except Exception:
                # A transient Redis error must not mass-disconnect live
                # sockets; expiry is still enforced locally above. Log and
                # retry on the next tick (fail-open on the denylist probe
                # only — the same posture as a Redis blip on the HTTP path).
                logger.bind(
                    event="ws_denylist_check_error",
                    connection_id=str(conn.connection_id),
                ).warning("ws denylist re-check failed; retrying next tick")
                continue
            if denied:
                _request_close(_CLOSE_AUTH_FAILED, "token revoked")
                return

    tasks = [
        asyncio.create_task(_reader(), name=f"ws-reader-{conn.connection_id}"),
        asyncio.create_task(_writer(), name=f"ws-writer-{conn.connection_id}"),
    ]
    writer_task = tasks[1]
    if channels:
        tasks.append(
            asyncio.create_task(
                _pubsub_fanin(),
                name=f"ws-fanin-{conn.connection_id}",
            ),
        )
    # SEC-H2: only run the watchdog when the caller supplied token metadata.
    # Without this guard a caller that opts out (token_expires_at=None) would
    # still get a task that loops forever — harmless, but the explicit gate
    # keeps the no-metadata path (tests) free of a Redis-touching background
    # task. With metadata, expiry is always enforced; denylist when reachable.
    if token_expires_at is not None or token_jti is not None or authorize is not None:
        tasks.append(
            asyncio.create_task(
                _auth_watchdog(),
                name=f"ws-authwatch-{conn.connection_id}",
            ),
        )

    try:
        # Run until the first task ends — client disconnect, idle timeout,
        # slow consumer, auth failure — or connection_loop itself being
        # cancelled (app shutdown).
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # ASYNC-13: wind every driver task down BEFORE the single ws.close(),
        # no matter how the await above exited — normal completion *or*
        # connection_loop itself being cancelled. The close must never race a
        # live _writer, so it happens only once every task has stopped.
        shutdown.set()
        # `_reader` / `_pubsub_fanin` block on I/O that does not observe
        # `shutdown`, so cancel them; neither writes to the socket, so
        # cancellation is clean. The writer observes `shutdown` and returns
        # between frames — grant it a short grace period before
        # force-cancelling, so it is not torn down mid-send_text, while a
        # writer wedged on a half-open socket still cannot stall teardown.
        for t in tasks:
            if t is not writer_task and not t.done():
                t.cancel()
        finished, _ = await asyncio.wait({writer_task}, timeout=_WRITER_DRAIN_GRACE_SECONDS)
        if not finished:
            writer_task.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception as exc:
                logger.bind(event="ws_task_error").exception(
                    "ws task failed",
                    exc_info=exc,
                )
        await _cleanup(conn, on_close)
        # ASYNC-13: the single, authoritative close — every driver task has
        # stopped above, so this never races an in-flight send_text. Suppressed
        # because the socket may already be closed (client-initiated
        # disconnect, or a close already sent by the ASGI server).
        with suppress(Exception):
            await ws.close(code=close_code, reason=close_reason)


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
