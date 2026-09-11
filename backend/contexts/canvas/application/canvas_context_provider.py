"""Canvas context provider -- injects canvas digest into the agent system prompt.

Follows the ActivityContextProvider pattern: best-effort, never raises into the
calling turn.  Returns a formatted ``[Canvas content]`` block or ``None``.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.canvas.application.canvas_service import CanvasService
from contexts.canvas.infrastructure.repositories import CanvasRepository

_log = logging.getLogger(__name__)

_MAX_DIGEST_CHARS = 2000


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
        """
        try:
            repo = CanvasRepository(self._db)
            canvas = await repo.get_by_chatroom(chatroom_id)
            if canvas is None or not canvas.expose_to_agents:
                return None
            service = CanvasService(self._db)
            digest = await service.latest_digest(canvas.id)
            if not digest:
                return None
            truncated = digest[:_MAX_DIGEST_CHARS]
            return f"[Canvas content]\n{truncated}"
        except Exception:
            _log.warning("canvas context fetch failed for room %s", chatroom_id, exc_info=True)
            return None


__all__ = ["CanvasContextProvider"]
