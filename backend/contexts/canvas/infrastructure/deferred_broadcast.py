"""Deferred CRDT broadcast -- enqueue a yjs-update for after_commit.

Lives in infrastructure/ because it touches SQLAlchemy session events.
The application layer and the facade both call this; neither owns the
hook logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)


def enqueue_crdt_broadcast(
    db: AsyncSession,
    channel: str,
    delta_b64: str,
) -> None:
    """Schedule a CRDT broadcast to fire after the current transaction commits."""
    payload: dict[str, Any] = {"data": delta_b64}
    pending = db.info.setdefault("_pending_crdt_broadcasts", [])
    pending.append((channel, payload))
    if "_crdt_broadcast_hooked" not in db.info:
        db.info["_crdt_broadcast_hooked"] = True
        try:

            @event.listens_for(db.sync_session, "after_commit")
            def _drain(session: Any) -> None:
                broadcasts = list(session.info.pop("_pending_crdt_broadcasts", []))
                if not broadcasts:
                    return
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    return
                for ch, data in broadcasts:
                    task = loop.create_task(_best_effort_emit(ch, data))
                    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        except Exception:
            _log.debug("could not attach after_commit hook", exc_info=True)


async def _best_effort_emit(channel: str, data: dict[str, Any]) -> None:
    try:
        await Publisher(channel).emit("yjs-update", data)
    except Exception:
        _log.warning("deferred CRDT broadcast failed ch=%s", channel, exc_info=True)


__all__ = ["enqueue_crdt_broadcast"]
