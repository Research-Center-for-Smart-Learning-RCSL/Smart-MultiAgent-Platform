"""`/ws/chatroom/{id}` — chatroom fan-out + presence (R13.19, §22.14)."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, WebSocket

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.application.triggers import evaluate_presence_change
from contexts.conversation.domain.errors import ChatroomNotFound, ForbiddenInRoom
from contexts.conversation.infrastructure.drafts import ACTIVITY, SURFACES, DraftStore, normalise_key
from contexts.conversation.interfaces import PresenceTracker, room_channel
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.db.session import async_session, get_sessionmaker
from shared_kernel.realtime import (
    ChannelConnection,
    WsAuthError,
    authenticate_subprotocol,
    connection_loop,
)
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


async def _notify_presence(chatroom_id: uuid.UUID) -> None:
    """Re-arm the silence timer for the room's bound agents on join (R15.05b).

    Join-edge only: the empty-room pause this used to also drive on leave was
    retired (2026-07-27-wakeup-sweep-failure-isolation C2) -- the live roster
    read in `evaluate_silence_trigger` is now the sole, level-triggered
    authority on an empty room. Best-effort and out-of-band of the WS
    connection's own session: a failure here must not drop the socket.
    Commits its own short-lived session because ``on_users_present`` may
    write audit rows in future."""
    try:
        async with async_session() as db:
            await evaluate_presence_change(db, chatroom_id=chatroom_id)
            await db.commit()
    except Exception:  # pragma: no cover — defensive; presence is fire-and-forget
        _log.warning("presence-change dispatch failed for room %s", chatroom_id, exc_info=True)


@router.websocket("/ws/chatroom/{chatroom_id}")
async def ws_chatroom(ws: WebSocket, chatroom_id: uuid.UUID) -> None:
    try:
        auth = await authenticate_subprotocol(ws)
    except WsAuthError:
        await ws.close(code=4401)
        return

    # ACL: reuse the same resolver the HTTP router uses so any change in
    # room-access rules is picked up in both channels at once.
    sm = get_sessionmaker()
    try:
        async with sm() as session, session.begin():
            access = await resolve_room_access(
                session,
                principal=auth.principal,
                chatroom_id=chatroom_id,
            )
            ensure_can_read(access, is_admin=auth.principal.is_admin)
    except (ChatroomNotFound, ForbiddenInRoom):
        await ws.close(code=4403)
        return

    presence = PresenceTracker()
    publisher = Publisher(room_channel(chatroom_id))
    drafts = DraftStore()

    _last_typing_ts: float = 0.0
    _typing_throttle_s: float = 2.0
    # §32 ([R32.03]): "where no binding in a room holds the grant, the server stores
    # no drafts for that room". Resolved per connection rather than per frame,
    # because `on_client_message` closes over `presence` and `publisher` only and a
    # grant read needs a fresh session — the way `_notify_presence` takes one. At one
    # session per frame a 30-typist room would cost ~15 sessions a second on the
    # socket path; at one per connection it costs 30 reads for the whole lesson.
    #
    # **Re-resolved on a timer, not on an event.** The design this replaced named a
    # `chatroom.agents_changed` broadcast that does not exist — the settings write
    # emits an audit row and publishes nothing, and `chatrooms.py` constructs no
    # Publisher at all. Rather than invent a broadcast for one reader, the flag
    # carries the time it was resolved and goes stale after `_GRANT_TTL_S`. A grant
    # revoked mid-session therefore stops new writes within that window and the draft
    # TTL bounds what was already stored; a grant *added* mid-session starts
    # collecting at the same point. Both directions self-heal, and the lag is a
    # stated constant rather than a dependency on an event that may never fire.
    _drafts_readable: bool = False
    _grant_resolved_ts: float = float("-inf")
    _grant_ttl_s: float = 60.0
    # Separate from `_last_typing_ts` even though both use the same window: the two
    # frames arrive on the same burst timer, so one shared variable would let
    # whichever landed first swallow the other for two seconds.
    _last_draft_ts: float = 0.0
    # F-18: typing is asserted per connection but was never retracted when that
    # connection ended, so a typist with a second tab open left the indicator
    # pinned for every other member — `presence.leave` reports left=False while
    # a sibling connection remains, which suppressed the only publish `on_close`
    # made, and nothing else expires typing state. This route body runs once per
    # connection, so the flag is correctly connection-scoped.
    _typing_active: bool = False

    async def _retract_typing(conn: ChannelConnection) -> None:
        """Publish `typing.stop` only once this user has no typing connection left.

        The event carries a user id and nothing else, so it is read as "this
        user stopped typing" by every client. Retracting on one connection's
        behalf would blank the indicator while a sibling tab is still mid-burst
        — and since the client sends `typing.start` once per burst rather than
        per keystroke, it would stay blank until the user paused and began a new
        one. The refcount lives in Redis because the two connections may be on
        different backend workers.
        """
        nonlocal _typing_active
        _typing_active = False
        last = await presence.typing_stop(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        if last:
            await publisher.emit("typing.stop", {"user_id": str(conn.principal.user_id)})

    async def _may_store_drafts() -> bool:
        """Whether any binding in this room may read drafts, re-resolved on a timer.

        **Fails closed on everything.** A read that raises leaves the previous answer
        in place only until the window lapses, after which the failure yields
        ``False`` and the room stores nothing — the direction where a Redis or
        PostgreSQL fault costs a feature rather than a disclosure.

        Its own short-lived session, out of band of the WS connection's, for the
        reason ``_notify_presence`` takes one: a failure here must not poison a
        transaction the socket depends on, and there is no session on this path.
        """
        nonlocal _drafts_readable, _grant_resolved_ts
        now_ts = time.monotonic()
        if now_ts - _grant_resolved_ts < _grant_ttl_s:
            return _drafts_readable
        try:
            async with async_session() as db:
                _drafts_readable = await ConversationFacade(db).room_has_draft_reader(chatroom_id)
        except Exception:
            _log.warning("draft grant read failed for room %s; storing no drafts", chatroom_id, exc_info=True)
            _drafts_readable = False
        _grant_resolved_ts = now_ts
        return _drafts_readable

    async def _handle_draft(conn: ChannelConnection, msg: dict, *, clear: bool) -> None:
        """One `draft.update` / `draft.clear` frame ([R32.01]).

        **Nothing is published and nothing is evaluated.** A draft frame produces no
        room event, wakes no agent, does not re-arm the silence clock and is not
        counted by `every_n_messages` — the whole point of §32 is that the only path
        from a draft to a model is a tool the model chose to call. This handler
        deliberately contains no `publisher.emit` and no trigger call, and AC-9's
        test is the tripwire for someone "improving" it later.

        A malformed frame is dropped in silence. The client reports on a timer, so
        the next tick corrects anything transient, and an error reply would be a
        channel for probing the room's grant state.
        """
        surface = msg.get("surface")
        if not isinstance(surface, str) or surface not in SURFACES:
            return
        key = normalise_key(msg.get("key"))
        if surface == ACTIVITY and key is None:
            return
        if clear:
            # A clear is honoured whatever the grant says. A revoke between the write
            # and the send would otherwise strand the entry until its TTL, which is
            # the one case where the participant explicitly asked for it to go.
            await drafts.clear(room_id=chatroom_id, user_id=conn.principal.user_id, surface=surface, key=key)
            return
        if not await _may_store_drafts():
            return
        content = msg.get("content")
        if not isinstance(content, str):
            return
        await drafts.put(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            surface=surface,
            key=key,
            content=content,
        )

    async def on_client_message(conn: ChannelConnection, msg: dict) -> None:
        nonlocal _last_typing_ts, _typing_active, _last_draft_ts
        msg_type = msg.get("type")
        if msg_type == "draft.update":
            # The same 2s window the typing path uses, and for the same reason: the
            # client sends once per burst, and this bounds a client that does not.
            # A throttled frame is dropped rather than queued -- the next tick
            # carries the newer text anyway, so queueing would only ever store
            # something already superseded.
            now = time.monotonic()
            if now - _last_draft_ts < _typing_throttle_s:
                return
            _last_draft_ts = now
            await _handle_draft(conn, msg, clear=False)
            return
        if msg_type == "draft.clear":
            # Deliberately outside the throttle: a clear is the retraction, and
            # dropping one leaves unsent text readable that its author has just
            # sent or discarded. It is also bounded by nothing a user can drive
            # faster than their own send button.
            await _handle_draft(conn, msg, clear=True)
            return
        if msg_type == "typing.start":
            now = time.monotonic()
            if now - _last_typing_ts < _typing_throttle_s:
                return
            _last_typing_ts = now
            await presence.typing_start(
                room_id=chatroom_id,
                user_id=conn.principal.user_id,
                connection_id=conn.connection_id,
            )
            # Emitted unconditionally rather than only on the first typing
            # connection: a client that joined mid-burst has no other way to
            # learn the indicator should be on, and a repeat start is idempotent
            # for every receiver.
            await publisher.emit(msg_type, {"user_id": str(conn.principal.user_id)})
            # Tracks what was actually published — a throttled start returns
            # above without emitting, so it must not arm the retraction.
            _typing_active = True
        elif msg_type == "typing.stop":
            await _retract_typing(conn)

    async def on_heartbeat(conn: ChannelConnection) -> None:
        # Every inbound frame proves the socket is alive — keep this user's
        # presence in the room from lapsing under a live connection (R13.19).
        await presence.heartbeat(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        if _typing_active:
            # A burst sends no periodic frame of its own, so the typing
            # assertion would otherwise lapse mid-burst on a long one.
            await presence.typing_heartbeat(
                room_id=chatroom_id,
                user_id=conn.principal.user_id,
                connection_id=conn.connection_id,
            )

    async def authorize(conn: ChannelConnection) -> bool:
        # Re-resolve room access mid-socket so a revoked guest link / lost
        # membership / tightened ACL tears the connection down (SEC-H2). Reads
        # the current (possibly refreshed) principal off `conn`.
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

    async def on_open(conn: ChannelConnection) -> None:
        added, roster_size = await presence.join(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        if added:
            await publisher.emit(
                "presence.joined",
                {"user_id": str(conn.principal.user_id)},
            )
            # FIX-03: atomic Lua guarantees exactly one concurrent first-joiner
            # observes roster_size == 1, so the transition fires exactly once.
            # Must stay nested under `added` — the roster SADD is idempotent,
            # so a second tab/reconnect of an already-present lone user would
            # also see roster_size == 1 and must NOT re-fire the transition.
            if roster_size == 1:
                await _notify_presence(chatroom_id)

    async def on_close(conn: ChannelConnection) -> None:
        nonlocal _typing_active
        left, _roster_size = await presence.leave(
            room_id=chatroom_id,
            user_id=conn.principal.user_id,
            connection_id=conn.connection_id,
        )
        # F-18: a connection that goes away has stopped typing by definition.
        # Deliberately outside the `left` guard below — that guard is False
        # exactly in the case this fixes, where a sibling connection of the
        # same user keeps the roster entry alive. The retraction itself is
        # refcounted, so a sibling that is *also* typing keeps the indicator up.
        if _typing_active:
            await _retract_typing(conn)
        if left:
            await publisher.emit(
                "presence.left",
                {"user_id": str(conn.principal.user_id)},
            )

    await connection_loop(
        ws=ws,
        principal=auth.principal,
        subprotocol=auth.subprotocol,
        channels=[room_channel(chatroom_id)],
        token_expires_at=auth.expires_at,
        token_jti=auth.jti,
        on_open=on_open,
        on_close=on_close,
        on_client_message=on_client_message,
        on_heartbeat=on_heartbeat,
        authorize=authorize,
    )


__all__ = ["router"]
