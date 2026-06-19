"""Conversation-context pub/sub channel builder."""

from __future__ import annotations

import uuid


def room_channel(chatroom_id: uuid.UUID) -> str:
    return f"ws:room:{chatroom_id}"


__all__ = ["room_channel"]
