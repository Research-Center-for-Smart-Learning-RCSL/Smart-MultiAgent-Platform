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


__all__ = ["AgentGroup"]
