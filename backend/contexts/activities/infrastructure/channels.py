"""Activities-context pub/sub channel builders ([R33.03])."""

from __future__ import annotations

import uuid


def project_channel(project_id: uuid.UUID) -> str:
    return f"ws:project:{project_id}"


__all__ = ["project_channel"]
