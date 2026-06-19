"""Real-time primitives shared by every WS endpoint (§22.14).

No single bounded context owns the real-time plumbing — chatroom rooms,
workflow runs, RAG ingest and the admin audit tail all need the same
connection-level policies (subprotocol auth, per-user cap, backpressure,
in-socket refresh). Hosting these primitives in `shared_kernel` keeps the
contexts framework-free and lets `app/api/ws/*` stay thin.
"""

from __future__ import annotations

from shared_kernel.realtime.connection import ChannelConnection, connection_loop
from contexts.conversation.infrastructure.presence import PresenceTracker
from shared_kernel.realtime.pubsub import Publisher, Subscriber, publish
from shared_kernel.realtime.ws_auth import (
    WsAuthError,
    authenticate_subprotocol,
    mint_ws_ticket,
)

__all__ = [
    "ChannelConnection",
    "PresenceTracker",
    "Publisher",
    "Subscriber",
    "WsAuthError",
    "authenticate_subprotocol",
    "connection_loop",
    "mint_ws_ticket",
    "publish",
]
