"""Application-owned repository contracts for activity lifecycles."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from contexts.activities.domain.models import ActivityActivation, ActivityType


class ActivityTypeReader(Protocol):
    async def get(self, type_id: uuid.UUID) -> ActivityType | None: ...


class ActivityActivationRepository(Protocol):
    async def get(self, activation_id: uuid.UUID) -> ActivityActivation | None: ...

    async def get_active(self, chatroom_id: uuid.UUID) -> ActivityActivation | None: ...

    async def list_active_for_type(self, activity_type_id: uuid.UUID) -> Sequence[ActivityActivation]: ...

    async def get_active_for_update(self, chatroom_id: uuid.UUID) -> ActivityActivation | None: ...

    async def create_active(
        self,
        *,
        chatroom_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        started_by_user_id: uuid.UUID,
    ) -> uuid.UUID | None: ...

    async def end(self, activation_id: uuid.UUID) -> bool: ...


__all__ = ["ActivityActivationRepository", "ActivityTypeReader"]
