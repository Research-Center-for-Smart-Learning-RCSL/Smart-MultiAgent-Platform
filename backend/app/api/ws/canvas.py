"""`/ws/canvas/{canvas_id}` -- real-time CRDT sync for collaborative canvas editing.

Dedicated WebSocket endpoint isolated from chat traffic. Carries Yjs update
deltas and awareness frames via base64-encoded JSON messages. The backend
validates incoming updates via ``pycrdt`` before relay ([R13.51]).

Connection cap: 10 concurrent editors per canvas ([R13.53]).
Persistence: every 30s while editors connected + last-disconnect flush ([R13.52]).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket

from contexts.canvas.application.crdt_relay import CrdtUpdateError, get_crdt_relay
from contexts.canvas.infrastructure.channels import canvas_channel
from contexts.canvas.infrastructure.repositories import CanvasRepository
from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom
from shared_kernel.auth.clients import get_redis
from shared_kernel.auth.ratelimit import check_raw as rate_check_raw
from shared_kernel.db.session import get_sessionmaker
from shared_kernel.realtime import (
    ChannelConnection,
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

_MAX_EDITORS_PER_CANVAS = 10
_CLOSE_CANVAS_FULL = 4009
_FLUSH_INTERVAL_SECONDS = 30.0
_GUEST_WS_RATE_LIMIT = 60  # operations per minute per guest ([R13.46])
_CANVAS_FRAME_BYTES = 256 * 1024  # 256 KB for Yjs update batches


def _editors_key(canvas_id: uuid.UUID) -> str:
    return f"ws:canvas-editors:{canvas_id}"


async def _register_editor(canvas_id: uuid.UUID, conn_id: uuid.UUID) -> int:
    """Add editor to the canvas ZSET. Returns current count."""
    r = get_redis()
    key = _editors_key(canvas_id)
    now = time.time()
    # Prune stale entries (crashed without cleanup)
    await r.zremrangebyscore(key, "-inf", now - 300)
    await r.zadd(key, {str(conn_id): now})
    count = await r.zcard(key)
    await r.expire(key, 300)
    return count


async def _touch_editor(canvas_id: uuid.UUID, conn_id: uuid.UUID) -> None:
    r = get_redis()
    key = _editors_key(canvas_id)
    await r.zadd(key, {str(conn_id): time.time()})
    await r.expire(key, 300)


async def _unregister_editor(canvas_id: uuid.UUID, conn_id: uuid.UUID) -> int:
    """Remove editor. Returns remaining count."""
    r = get_redis()
    key = _editors_key(canvas_id)
    await r.zrem(key, str(conn_id))
    count = await r.zcard(key)
    return count


@router.websocket("/ws/canvas/{canvas_id}")
async def ws_canvas(ws: WebSocket, canvas_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return

    sm = get_sessionmaker()

    # Resolve canvas -> chatroom -> ACL
    async with sm() as session:
        repo = CanvasRepository(session)
        canvas = await repo.get(canvas_id)
        if canvas is None:
            await ws.close(code=4404)
            return
        chatroom_id = canvas.chatroom_id

        try:
            access = await resolve_room_access(
                session,
                principal=auth.principal,
                chatroom_id=chatroom_id,
            )
            ensure_can_read(access, is_admin=auth.principal.is_admin)
        except (ChatroomNotFound, ForbiddenInRoom):
            await ws.close(code=4403)
            return

        crdt_state = canvas.crdt_state
        legacy_objects = None
        if crdt_state is None:
            legacy_objects = await repo.list_objects(canvas_id)

    # Editor cap check ([R13.53]) -- before connection_loop so the close code
    # is ours (4009), not connection_loop's generic 1008.
    _temp_conn_id = uuid.uuid4()
    editor_count = await _register_editor(canvas_id, _temp_conn_id)
    if editor_count > _MAX_EDITORS_PER_CANVAS:
        await _unregister_editor(canvas_id, _temp_conn_id)
        await ws.accept(subprotocol=auth.subprotocol)
        await ws.close(code=_CLOSE_CANVAS_FULL, reason="canvas editor limit reached")
        return
    # Unregister the temp entry; on_open will register the real connection_id.
    await _unregister_editor(canvas_id, _temp_conn_id)

    relay = get_crdt_relay()
    publisher = Publisher(canvas_channel(canvas_id))

    _last_flush_ts: float = time.monotonic()

    async def _check_guest_rate(conn: ChannelConnection) -> bool:
        """Returns False if guest rate limit exceeded."""
        if not conn.principal.is_guest:
            return True
        guest_id = str(conn.principal.user_id)
        decision = await rate_check_raw(
            key=f"rl:canvas-ws-guest:{guest_id}",
            window_sec=60,
            max_count=_GUEST_WS_RATE_LIMIT,
        )
        return decision.allowed

    async def _flush_if_needed() -> None:
        nonlocal _last_flush_ts
        if not relay.should_flush(canvas_id):
            return
        state_bytes = relay.get_state_bytes(canvas_id)
        if state_bytes is None:
            return
        async with sm() as session:
            repo = CanvasRepository(session)
            await repo.update_crdt_state(canvas_id, state_bytes)
            await session.commit()
        relay.mark_flushed(canvas_id)
        _last_flush_ts = time.monotonic()

    async def on_open(conn: ChannelConnection) -> None:
        nonlocal _last_flush_ts
        await _register_editor(canvas_id, conn.connection_id)

        # Load or migrate the CRDT doc
        await relay.get_or_load(
            canvas_id,
            crdt_state=crdt_state,
            legacy_objects=legacy_objects or None,
        )

        # If this was a migration, persist immediately
        if crdt_state is None and legacy_objects:
            state_bytes = relay.get_state_bytes(canvas_id)
            if state_bytes:
                async with sm() as session:
                    repo = CanvasRepository(session)
                    await repo.update_crdt_state(canvas_id, state_bytes)
                    await session.commit()
                relay.mark_flushed(canvas_id)

        # Send initial state to client (sync step 1)
        state_b64 = relay.get_state_as_b64(canvas_id)
        if state_b64:
            await conn.enqueue({
                "type": "yjs-sync-step-1",
                "data": state_b64,
            })

        _last_flush_ts = time.monotonic()

    async def on_close(conn: ChannelConnection) -> None:
        remaining = await _unregister_editor(canvas_id, conn.connection_id)
        if remaining == 0:
            # Last editor disconnected -- flush and evict
            await _flush_if_needed()
            relay.evict(canvas_id)

    async def on_client_message(conn: ChannelConnection, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type")

        if msg_type == "yjs-update":
            if not await _check_guest_rate(conn):
                await conn.enqueue({"type": "error", "message": "rate limit exceeded"})
                return
            data = msg.get("data")
            if not isinstance(data, str):
                return
            try:
                delta_b64 = await relay.apply_update(canvas_id, data)
            except CrdtUpdateError as exc:
                await conn.enqueue({
                    "type": "error",
                    "message": f"update rejected: {exc}",
                })
                return
            # Relay to all other editors via the canvas channel
            await publisher.emit("yjs-update", {"data": delta_b64})

        elif msg_type == "yjs-sync-step-1":
            # Client is sending its state vector, server responds with missing updates
            state_b64 = relay.get_state_as_b64(canvas_id)
            if state_b64:
                await conn.enqueue({
                    "type": "yjs-sync-step-2",
                    "data": state_b64,
                })

        elif msg_type == "yjs-sync-step-2":
            # Client sending missing updates to server
            data = msg.get("data")
            if not isinstance(data, str):
                return
            try:
                await relay.apply_update(canvas_id, data)
            except CrdtUpdateError:
                pass  # Sync step 2 failures are non-fatal

        elif msg_type == "awareness":
            if not await _check_guest_rate(conn):
                return
            data = msg.get("data")
            if not isinstance(data, (str, dict)):
                return
            # Pass-through: awareness is ephemeral, not persisted
            await publisher.emit("awareness", {
                "data": data,
                "user_id": str(conn.principal.user_id),
            })

    async def on_heartbeat(conn: ChannelConnection) -> None:
        nonlocal _last_flush_ts
        await _touch_editor(canvas_id, conn.connection_id)
        # Periodic flush
        now_ts = time.monotonic()
        if now_ts - _last_flush_ts >= _FLUSH_INTERVAL_SECONDS:
            try:
                await _flush_if_needed()
            except Exception:
                _log.warning("periodic flush failed for canvas %s", canvas_id, exc_info=True)
            _last_flush_ts = now_ts

    async def authorize(conn: ChannelConnection) -> bool:
        try:
            async with sm() as session, session.begin():
                access = await resolve_room_access(
                    session,
                    principal=conn.principal,
                    chatroom_id=chatroom_id,
                )
                ensure_can_read(access, is_admin=conn.principal.is_admin)
            return True
        except (ChatroomNotFound, ForbiddenInRoom):
            return False

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[canvas_channel(canvas_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
        on_open=on_open,
        on_close=on_close,
        on_client_message=on_client_message,
        on_heartbeat=on_heartbeat,
        authorize=authorize,
        max_frame_bytes=_CANVAS_FRAME_BYTES,
    )


__all__ = ["router"]
