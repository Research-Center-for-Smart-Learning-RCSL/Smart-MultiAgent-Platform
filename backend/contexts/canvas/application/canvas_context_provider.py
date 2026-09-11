"""Canvas context provider -- injects canvas digest into the agent system prompt.

Follows the ActivityContextProvider pattern: best-effort, never raises into the
calling turn.  Returns a formatted ``[Canvas content]`` block or ``None``.

Fallback chain for digest: in-memory CRDT doc -> persisted crdt_state -> legacy
snapshot digest ([R13.47] behavior change).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC
from typing import Any

import pycrdt
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.application.canvas_service import CanvasService
from contexts.canvas.application.crdt_relay import get_crdt_relay
from contexts.canvas.domain.canvas_digest import build_canvas_digest
from contexts.canvas.domain.models import CanvasObject, CanvasObjectKind
from contexts.canvas.infrastructure.repositories import CanvasRepository

_log = logging.getLogger(__name__)

_MAX_DIGEST_CHARS = 2000


def _elements_to_pseudo_objects(elements: list[dict[str, Any]]) -> list[CanvasObject]:
    """Convert Excalidraw element dicts to CanvasObject-like for digest building."""
    from datetime import datetime

    now = datetime.now(UTC)
    type_map = {
        "rectangle": CanvasObjectKind.SHAPE,
        "ellipse": CanvasObjectKind.SHAPE,
        "diamond": CanvasObjectKind.SHAPE,
        "text": CanvasObjectKind.TEXT,
        "image": CanvasObjectKind.IMAGE,
        "freedraw": CanvasObjectKind.DRAWING,
        "arrow": CanvasObjectKind.CONNECTOR,
        "line": CanvasObjectKind.CONNECTOR,
    }
    result: list[CanvasObject] = []
    for elem in elements:
        kind = type_map.get(elem.get("type", ""), CanvasObjectKind.SHAPE)
        result.append(
            CanvasObject(
                id=uuid.UUID(elem["id"]) if "id" in elem else uuid.uuid4(),
                canvas_id=uuid.UUID(int=0),
                kind=kind,
                position_x=elem.get("x", 0),
                position_y=elem.get("y", 0),
                width=elem.get("width", 0),
                height=elem.get("height", 0),
                z_index=0,
                content=elem.get("text"),
                minio_path=elem.get("fileId"),
                created_at=now,
                updated_at=now,
            )
        )
    return result


def _digest_from_crdt_state(crdt_state: bytes) -> str | None:
    """Build digest from persisted CRDT state bytes."""
    try:
        doc: pycrdt.Doc = pycrdt.Doc()  # type: ignore[type-arg]
        doc.apply_update(crdt_state)
        elements = doc.get("elements", type=pycrdt.Array)
        elem_dicts = []
        for item in elements:
            if isinstance(item, pycrdt.Map):
                d = dict(item)
            elif isinstance(item, dict):
                d = item
            else:
                continue
            if not d.get("isDeleted"):
                elem_dicts.append(d)
        if not elem_dicts:
            return None
        pseudo_objects = _elements_to_pseudo_objects(elem_dicts)
        return build_canvas_digest(pseudo_objects)
    except Exception:
        return None


class CanvasContextProvider:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def query(
        self,
        *,
        chatroom_id: uuid.UUID,
    ) -> str | None:
        """Return a ``[Canvas content]`` block for the room, or ``None``.

        ``None`` when: no canvas exists, ``expose_to_agents`` is false, the
        canvas has no objects, or on any failure.

        Fallback chain: in-memory CRDT -> persisted crdt_state -> legacy snapshot.
        """
        try:
            repo = CanvasRepository(self._db)
            canvas = await repo.get_by_chatroom(chatroom_id)
            if canvas is None or not canvas.expose_to_agents:
                return None

            # Try in-memory CRDT doc first
            relay = get_crdt_relay()
            if relay.has(canvas.id):
                elements = relay.extract_elements_for_digest(canvas.id)
                if elements:
                    pseudo_objects = _elements_to_pseudo_objects(elements)
                    digest = build_canvas_digest(pseudo_objects)
                    if digest:
                        return f"[Canvas content]\n{digest[:_MAX_DIGEST_CHARS]}"

            # Try persisted crdt_state
            if canvas.crdt_state:
                digest = _digest_from_crdt_state(canvas.crdt_state)
                if digest:
                    return f"[Canvas content]\n{digest[:_MAX_DIGEST_CHARS]}"

            # Fall back to legacy snapshot digest
            service = CanvasService(self._db)
            digest = await service.latest_digest(canvas.id)
            if not digest:
                return None
            return f"[Canvas content]\n{digest[:_MAX_DIGEST_CHARS]}"
        except Exception:
            _log.warning("canvas context fetch failed for room %s", chatroom_id, exc_info=True)
            return None


__all__ = ["CanvasContextProvider"]
