"""Knowledge-context pub/sub channel builder."""

from __future__ import annotations

import uuid


def rag_channel(config_id: uuid.UUID) -> str:
    return f"ws:rag:{config_id}"


__all__ = ["rag_channel"]
