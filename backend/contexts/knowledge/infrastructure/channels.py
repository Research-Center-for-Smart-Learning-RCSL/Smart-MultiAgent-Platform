"""Knowledge-context pub/sub channel builder."""

from __future__ import annotations

import uuid


def rag_channel(config_id: uuid.UUID) -> str:
    return f"ws:rag:{config_id}"


def graphrag_channel(config_id: uuid.UUID) -> str:
    return f"ws:graphrag:{config_id}"


__all__ = ["graphrag_channel", "rag_channel"]
