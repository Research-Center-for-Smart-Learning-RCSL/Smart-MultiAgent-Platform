"""Redis pub/sub wrapper (§22.14).

Redis pub/sub is fire-and-forget — messages published while a subscriber is
disconnected are lost, which matches the R13.20 contract ("server does not
replay; client fetches delta on reconnect"). If load ever forces us to
Streams the `Publisher` / `Subscriber` shape stays the same; only the
implementation changes.

Channel naming (flat namespace, no project prefix needed — room IDs are
already UUIDs):

  ws:user:{user_id}       — user notifications (ban, RAG status, etc.)
  ws:room:{room_id}       — chatroom events (R13.19 set)
  ws:wf:{run_id}          — workflow run progress
  ws:rag:{config_id}      — RAG config events
  ws:audit:tail           — admin audit tail (Admin-only)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio.client import PubSub

from shared_kernel.auth.clients import get_redis

# ---------------------------------------------------------------------------
# Backward-compat re-exports (DEPRECATED) — lazy to avoid circular imports.
# Canonical locations:
#   room_channel       -> contexts.conversation.infrastructure.channels
#   workflow_channel   -> contexts.workflow.infrastructure.channels
#   rag_channel        -> contexts.knowledge.infrastructure.channels
#   AUDIT_TAIL_CHANNEL -> contexts.audit.infrastructure.channels
#   user_channel       -> contexts.identity.infrastructure.channels
# ---------------------------------------------------------------------------
_CHANNEL_REEXPORTS: dict[str, tuple[str, str]] = {
    "AUDIT_TAIL_CHANNEL": ("contexts.audit.infrastructure.channels", "AUDIT_TAIL_CHANNEL"),
    "room_channel": ("contexts.conversation.infrastructure.channels", "room_channel"),
    "user_channel": ("contexts.identity.infrastructure.channels", "user_channel"),
    "rag_channel": ("contexts.knowledge.infrastructure.channels", "rag_channel"),
    "workflow_channel": ("contexts.workflow.infrastructure.channels", "workflow_channel"),
}


def __getattr__(name: str) -> Any:
    if name in _CHANNEL_REEXPORTS:
        mod_path, attr = _CHANNEL_REEXPORTS[name]
        import importlib

        return getattr(importlib.import_module(mod_path), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def publish(channel: str, event: dict[str, Any]) -> int:
    """Publish a JSON-serialised event. Returns number of subscribers."""
    payload = json.dumps(event, default=_json_default, separators=(",", ":"))
    return int(await get_redis().publish(channel, payload))


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    # Delegate dates/enums to their str so callers don't have to pre-coerce.
    return str(value)


class Publisher:
    """Thin helper so call sites read `Publisher(room_channel(id)).emit(...)`
    rather than the raw `publish(channel, ...)` function. Provides symmetry
    with `Subscriber` and makes injection easier in tests."""

    def __init__(self, channel: str) -> None:
        self._channel = channel

    async def emit(self, event_type: str, data: dict[str, Any] | None = None) -> int:
        payload: dict[str, Any] = {"type": event_type}
        if data:
            payload.update(data)
        return await publish(self._channel, payload)


class Subscriber:
    """Context-managed pub/sub subscription.

    Usage:
        async with Subscriber([room_channel(room_id)]) as sub:
            async for event in sub.events():
                ...
    """

    def __init__(self, channels: list[str]) -> None:
        self._channels = channels
        self._pubsub: PubSub | None = None

    async def __aenter__(self) -> Subscriber:
        r = get_redis()
        self._pubsub = r.pubsub()
        await self._pubsub.subscribe(*self._channels)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(*self._channels)
            finally:
                await self._pubsub.aclose()
            self._pubsub = None

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        assert self._pubsub is not None, "Subscriber not entered"
        async for msg in self._pubsub.listen():
            if msg.get("type") != "message":
                continue
            raw = msg.get("data")
            if raw is None:
                continue
            try:
                yield json.loads(raw)
            except (TypeError, json.JSONDecodeError):  # pragma: no cover
                continue


__all__ = [
    "Publisher",
    "Subscriber",
    "publish",
]
