"""prompt_studio facade — cross-context read surface (§29).

The context is otherwise self-contained (its own API routes call the
application services directly); the facade exposes what other contexts (and
the WS transport layer, which sits outside any context) need without reaching
into application/infrastructure directly.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.prompt_studio.application.config_service import ConfigService
from contexts.prompt_studio.application.session_service import SessionService
from contexts.prompt_studio.domain.errors import SessionNotFound
from contexts.prompt_studio.domain.models import AssistantConfig


class PromptStudioFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def resolve_config_for_project(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> AssistantConfig | None:
        return await ConfigService(self._db).resolve_for_project(project_id=project_id, user_id=user_id)

    async def verify_session_owner(self, session_id: uuid.UUID, actor_user_id: uuid.UUID) -> bool:
        """True iff *session_id* exists and belongs to *actor_user_id*.

        Collapses "no such session" and "wrong owner" into one False outcome —
        never leaks whether a session id exists to a non-owner (mirrors
        SessionService.require_owned_session, used by the WS transport layer,
        which cannot import application services directly).
        """
        try:
            await SessionService(self._db).require_owned_session(session_id, actor_user_id)
        except SessionNotFound:
            return False
        return True


__all__ = ["PromptStudioFacade"]
