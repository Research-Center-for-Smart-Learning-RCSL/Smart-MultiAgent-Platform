"""Redis pub/sub channel names for the prompt_studio context (§29)."""

from __future__ import annotations

import uuid


def prompt_assistant_channel(session_id: uuid.UUID) -> str:
    return f"ws:prompt:{session_id}"


__all__ = ["prompt_assistant_channel"]
