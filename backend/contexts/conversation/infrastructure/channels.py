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


__all__ = ["emit_agent_finished_error", "room_channel"]
