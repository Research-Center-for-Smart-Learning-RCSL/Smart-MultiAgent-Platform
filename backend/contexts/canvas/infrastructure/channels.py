"""Canvas-context pub/sub channel builder."""

from __future__ import annotations

import uuid


def canvas_channel(canvas_id: uuid.UUID) -> str:
    return f"ws:canvas:{canvas_id}"


__all__ = ["canvas_channel"]
