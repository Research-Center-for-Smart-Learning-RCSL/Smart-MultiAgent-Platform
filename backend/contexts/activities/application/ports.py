"""Application-owned repository contracts for activity lifecycles."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from contexts.activities.domain.models import ActivityActivation, ActivityType


class ActivityTypeReader(Protocol):
    async def get(self, type_id: uuid.UUID) -> ActivityType | None: ...


class ActivityTypeOptInReader(Protocol):
    """The authorization read behind ``resolve_reachable_type`` ([R30.33]).

    Narrower than the repository on purpose: the reachability check must not be
    able to reach a query that answers anything more permissive than "does this
    exact opt-in row exist".
    """

    async def exists(self, *, project_id: uuid.UUID, activity_type_id: uuid.UUID) -> bool: ...


class ActivitySessionCloser(Protocol):
    """The one session write ending a round performs ([R30.22]).

    Deliberately a single method rather than the whole session repository:
    ``ActivationService`` has no business reading or opening sessions, and a
    wider contract here would let it grow that way without anyone noticing.
    """

    async def close_open_for_activation(self, activation_id: uuid.UUID) -> int: ...


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
        started_by_agent_id: uuid.UUID | None = None,
    ) -> uuid.UUID | None: ...

    async def end(self, activation_id: uuid.UUID) -> bool: ...


__all__ = [
    "ActivityActivationRepository",
    "ActivitySessionCloser",
    "ActivityTypeOptInReader",
    "ActivityTypeReader",
]
