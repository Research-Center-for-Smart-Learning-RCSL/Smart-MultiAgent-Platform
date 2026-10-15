"""A bounded, failure-aware reading of who is connected to a room ([R13.74]).

``ConversationFacade.present_user_ids`` answers "nobody" when Redis cannot be
read, which is right for the member roster it serves (naming fewer people is
harmless there) and wrong for an agent's participant facts, where "nobody is
connected" is a claim the agent would act on. This read keeps the two apart.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RoomPresenceSnapshot:
    observed_at: datetime
    # False when the roster could not be read; ``user_ids`` is then empty and
    # means nothing.
    available: bool
    # User ids and anonymous guest session ids with a live connection, id order.
    user_ids: tuple[uuid.UUID, ...]
    # More connected principals exist than ``user_ids`` holds.
    truncated: bool


__all__ = ["RoomPresenceSnapshot"]
