"""Observer context provider — recent structured activity as a system block (§30, R30.15).

Mirrors the RAG/knowledge-map context providers (their home is
``<context>/application/``, imported directly by the turn engine): a ``query(...)
-> str | None`` returning a formatted ``[Recent room activity]`` block or ``None``.
Best-effort: any failure degrades to ``None`` and never breaks the observer turn
(R30.16). Reads only deterministic, server-computed facts — no LLM inference.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import RecentActivityRow, ValidationStatus

_log = logging.getLogger(__name__)

DEFAULT_ACTIVITY_WINDOW = 30


class ActivityContextProvider:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def query(self, *, chatroom_id: uuid.UUID, limit: int = DEFAULT_ACTIVITY_WINDOW) -> str | None:
        """Return a ``[Recent room activity]`` block for the room, or ``None`` when
        the room has no activity events (coverage gate) or on any failure."""
        # Lazy import keeps the activities facade off this module's import graph
        # until a turn actually needs it (parallels the engine's lazy facades).
        from contexts.activities.interfaces.facade import ActivitiesFacade

        try:
            rows = await ActivitiesFacade(self._db).list_recent_activity(chatroom_id, limit)
        except Exception:
            _log.warning("activity context fetch failed for room %s", chatroom_id, exc_info=True)
            return None
        if not rows:
            return None
        lines = [_format_row(r) for r in rows]
        return "[Recent room activity]\n" + "\n".join(lines)


def _format_row(row: RecentActivityRow) -> str:
    ts = row.created_at.isoformat() if row.created_at else "?"
    subject = f"u:{str(row.subject_user_id)[:8]}"
    outcome = _outcome(row.validation_status, row.is_valid)
    suffix = f" [{row.error_class}]" if row.error_class else ""
    return f"- ({ts}) {subject} #{row.attempt_no} {row.type_key}: {outcome}{suffix}"


def _outcome(status: ValidationStatus, is_valid: bool | None) -> str:
    if status is ValidationStatus.PENDING:
        return "pending"
    if status is ValidationStatus.ERROR:
        return "error"
    return "valid" if is_valid else "invalid"


__all__ = ["ActivityContextProvider"]
