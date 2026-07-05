"""prompt_studio facade — cross-context read surface (§29).

The context is self-contained today (its own API routes call the application
services directly), so the facade exposes only the effective-config resolution
that a future consumer would need. It follows the standard layout and keeps the
import graph explicit.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.prompt_studio.application.config_service import ConfigService
from contexts.prompt_studio.domain.models import AssistantConfig


class PromptStudioFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def resolve_config_for_project(
        self, *, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> AssistantConfig | None:
        return await ConfigService(self._db).resolve_for_project(project_id=project_id, user_id=user_id)


__all__ = ["PromptStudioFacade"]
