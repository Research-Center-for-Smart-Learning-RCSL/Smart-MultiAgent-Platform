"""Activity context provider — recent structured activity as a system block (§30, R30.15).

Mirrors the RAG/knowledge-map context providers (their home is
``<context>/application/``, imported directly by the turn engine): a ``query(...)
-> str | None`` returning a formatted ``[Recent room activity]`` block or ``None``.
Best-effort: any failure degrades to ``None`` and never breaks the calling turn
(R30.16). Given to every agent's turn, not just observers (agent-visibility
follow-up) — each row's submission content is included only when that row's
``ActivityType.expose_payload_to_agent`` allows it; outcome fields (attempt#,
valid/invalid, error class) are always deterministic, server-computed facts, never
LLM inference.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import RecentActivityRow, ValidationStatus

if TYPE_CHECKING:
    # Type-only: `from __future__ import annotations` keeps the annotation a
    # string, so the facade stays off this module's runtime import graph.
    from contexts.activities.interfaces.facade import ActivitiesFacade

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

        facade = ActivitiesFacade(self._db)
        try:
            rows = await facade.list_recent_activity(chatroom_id, limit)
        except Exception:
            _log.warning("activity context fetch failed for room %s", chatroom_id, exc_info=True)
            return None
        if not rows:
            return None
        digests_allowed = await self._digests_allowed(facade)
        lines = [_format_row(r, digests_allowed=digests_allowed) for r in rows]
        return "[Recent room activity]\n" + "\n".join(lines)

    async def _digests_allowed(self, facade: ActivitiesFacade) -> bool:
        """Whether the platform policy still permits submission content in a prompt.

        Both enforcement gates ([R30.30]) run before a room goes live — authoring
        and activation start — so an admin who locks
        ``expose_payload_to_agent=false`` mid-class would otherwise keep feeding
        that room's answers to every agent until someone ends the activity. This
        switch exists for consent, and consent withdrawn has to take effect now,
        not at the next activation. One indexed single-row read per turn.

        Fails closed: a policy that cannot be read withholds content rather than
        assuming permission. Only the digests are dropped, not the whole block —
        the outcome fields are server-computed facts that carry no answer text.
        """
        try:
            policy = await facade.get_activity_policy()
        except Exception:
            _log.warning("activity policy read failed; withholding submission content", exc_info=True)
            return False
        return not (policy.expose_payload_to_agent_locked and not policy.expose_payload_to_agent_default)


def _format_row(row: RecentActivityRow, *, digests_allowed: bool) -> str:
    ts = row.created_at.isoformat() if row.created_at else "?"
    subject = f"u:{str(row.subject_user_id)[:8]}"
    outcome = _outcome(row.validation_status, row.is_valid)
    suffix = f" [{row.error_class}]" if row.error_class else ""
    line = f"- ({ts}) {subject} #{row.attempt_no} {row.type_key}: {outcome}{suffix}"
    if digests_allowed and row.expose_payload_to_agent and row.agent_digest:
        line += f" — {row.agent_digest}"
    return line


def _outcome(status: ValidationStatus, is_valid: bool | None) -> str:
    if status is ValidationStatus.PENDING:
        return "pending"
    if status is ValidationStatus.ERROR:
        return "error"
    return "valid" if is_valid else "invalid"


__all__ = ["ActivityContextProvider"]
