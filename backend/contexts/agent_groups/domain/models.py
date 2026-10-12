"""Agent-groups domain read models (Phase 4α backend).

A thin, transport-agnostic view of a group row for the list/get read surface the
frontend re-home (Phase 4α) consumes. Kept separate from the SQLAlchemy row so the
service/facade never leak ORM types to the web layer.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentGroup:
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    concept_map_enabled: bool
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class AgentGroupMember:
    """A live member and what it reads from the group's Concept Map (R11.22).

    ``read_member_ids`` is ``None`` for the whole group; otherwise the members
    whose contributions it reads, possibly empty (reads nothing).
    """

    agent_id: uuid.UUID
    read_member_ids: tuple[uuid.UUID, ...] | None


__all__ = ["AgentGroup", "AgentGroupMember"]
