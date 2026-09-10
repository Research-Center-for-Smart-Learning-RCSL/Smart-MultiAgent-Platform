"""Domain models for the canvas bounded context."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class CanvasObjectKind(str, enum.Enum):
    NOTE = "note"
    TEXT = "text"
    IMAGE = "image"
    SHAPE = "shape"
    DRAWING = "drawing"
    CONNECTOR = "connector"


@dataclass(frozen=True, slots=True)
class Canvas:
    id: uuid.UUID
    chatroom_id: uuid.UUID
    expose_to_agents: bool
    created_at: datetime
    deleted_at: datetime | None = None
    crdt_state: bytes | None = None


@dataclass(frozen=True, slots=True)
class CanvasObject:
    id: uuid.UUID
    canvas_id: uuid.UUID
    kind: CanvasObjectKind
    position_x: float
    position_y: float
    width: float
    height: float
    z_index: int
    created_at: datetime
    updated_at: datetime
    content: str | None = None
    minio_path: str | None = None
    style: dict[str, Any] = field(default_factory=dict)
    created_by_user_id: uuid.UUID | None = None
    created_by_guest_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class CanvasSnapshot:
    id: uuid.UUID
    canvas_id: uuid.UUID
    snapshot_data: dict[str, Any]
    created_at: datetime
    agent_digest: str | None = None
    created_by_user_id: uuid.UUID | None = None


__all__ = [
    "Canvas",
    "CanvasObject",
    "CanvasObjectKind",
    "CanvasSnapshot",
]
