"""Identity-context pub/sub channel builder."""

from __future__ import annotations

import uuid


def user_channel(user_id: uuid.UUID) -> str:
    return f"ws:user:{user_id}"


__all__ = ["user_channel"]
