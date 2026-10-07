"""Conversation-context pub/sub channel builder + room emit helpers."""

from __future__ import annotations

import uuid

from loguru import logger

from shared_kernel.realtime.pubsub import Publisher


def room_channel(chatroom_id: uuid.UUID) -> str:
    return f"ws:room:{chatroom_id}"


async def emit_agent_finished_error(chatroom_id: uuid.UUID, agent_id: uuid.UUID, reason: str) -> None:
    """Best-effort 'why no reply' notice on a room channel.

    A skipped or failed turn otherwise just goes quiet on the client; this
    surfaces the cause under the ``error`` key (which the client toasts — a bare
    ``reason`` is treated as a benign silent skip). Swallows transport failures
    so a skip never escalates into a failed turn or a failed worker job.
    """
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "agent.finished", {"error": reason, "agent_id": str(agent_id)}
        )
    except Exception:
        logger.bind(agent_id=str(agent_id), room_id=str(chatroom_id), reason=reason).warning(
            "agent skip-notice emit failed"
        )


async def emit_members_changed(chatroom_id: uuid.UUID) -> None:
    """Tell the room's open clients to re-read the participant roster ([R13.19]).

    Ids only: the room channel has no per-recipient filtering, and each client's
    re-read answers for that client. Callers commit first so the frame never
    announces a write a rollback could undo. Swallows transport failure: the change
    is durable, and a missed refresh is reconciled by the client's reconnect re-read.
    """
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "chatroom.members_changed", {"chatroom_id": str(chatroom_id)}
        )
    except Exception:
        logger.bind(room_id=str(chatroom_id)).opt(exception=True).warning(
            "chatroom.members_changed emit failed"
        )


__all__ = ["emit_agent_finished_error", "emit_members_changed", "room_channel"]
