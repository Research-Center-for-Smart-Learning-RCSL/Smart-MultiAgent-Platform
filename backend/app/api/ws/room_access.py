"""Room-read probe outcome shared by the chat and canvas sockets (F-16).

Both sockets check room access at the handshake and again on the watchdog
cadence. Each used to catch its own fixed list of exceptions, and the lists
missed ``WorkspaceNotFound``: the handshake raised and the watchdog retried
forever, so a socket outlived its workspace. One mapping for all four call sites
keeps them from drifting again.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from contexts.conversation.interfaces.access import ChatroomNotFound, ForbiddenInRoom, WorkspaceNotFound
from shared_kernel.realtime import AccessOutcome


async def room_read_outcome(check: Callable[[], Awaitable[None]]) -> AccessOutcome:
    """Run a room-read check and say what it found.

    A deleted project reaches here as ``ChatroomNotFound`` (the resolver's own
    mapping) and a deleted workspace as ``WorkspaceNotFound``; both are GONE.
    Anything else propagates, so a transient fault is never read as a verdict.
    """
    try:
        await check()
    except (ChatroomNotFound, WorkspaceNotFound):
        return AccessOutcome.GONE
    except ForbiddenInRoom:
        return AccessOutcome.FORBIDDEN
    return AccessOutcome.ALLOWED


__all__ = ["room_read_outcome"]
