"""GraphRAG domain dataclasses — framework-free (E.7 / §11 / R11.01–R11.06).

GraphRAG augments chat with a persistent `(:Entity)-[:REL]->(:Entity)`
graph built from conversation history. Everything here is pure data:
- :class:`BuildState` is the 2PC state machine (R11.04 / §11.2a).
- :class:`GraphRagConfig` is the persisted row.
- :class:`Triple` is the atom extracted by the LLM (R11.03).
- :class:`GraphRagBundle` is the retrieval result (R11.06, ≤2 KB).
"""

from __future__ import annotations

import enum
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class BuildState(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    NEO4J_COMMITTED = "neo4j_committed"
    QDRANT_COMMITTED = "qdrant_committed"
    FAILED_COMPENSATING = "failed_compensating"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class GraphRagConfig:
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    trigger_config: dict[str, Any]
    last_build_at: datetime | None
    last_build_state: BuildState
    last_build_error: str | None
    created_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True, slots=True)
class GraphRagConfigDraft:
    agent_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    trigger_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Triple:
    """A single extracted relation (R11.03).

    ``evidence_msg_ids`` carries the chat message ids that justify the
    triple so downstream retrieval can surface evidence excerpts.

    ``subject_type`` / ``object_type`` are coarse entity categories (e.g.
    person, organization, concept) the extractor classifies the endpoints
    into, used to colour nodes in the graph visualizer (audit L1). Empty
    string means "unknown" — older builds and soft parse failures leave it
    blank, which the viewer renders as a neutral category.
    """

    subject: str
    relation: str
    object: str
    confidence: float
    evidence_msg_ids: tuple[uuid.UUID, ...]
    subject_type: str = ""
    object_type: str = ""


@dataclass(frozen=True, slots=True)
class EntityHit:
    """A single Qdrant hit for the GraphRAG entity collection."""

    entity: str
    score: float
    description: str


@dataclass(frozen=True, slots=True)
class RelationEdge:
    """A `(:Entity)-[:REL]->(:Entity)` edge returned by Neo4j traversal."""

    subject: str
    relation: str
    object: str
    confidence: float
    evidence_msg_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class GraphRagBundle:
    """Result of hybrid retrieval (R11.06) — serialisable under the 2 KB cap."""

    entities: tuple[str, ...]
    relations: tuple[RelationEdge, ...]
    evidence_excerpts: tuple[str, ...]

    def as_system_message(self) -> dict[str, Any]:
        """Render as a ``{"type":"graphrag"}`` system message (§22.8)."""
        payload = {
            "role": "system",
            "content": _render_bundle_text(self),
            "metadata": {"type": "graphrag"},
        }
        return _cap_to_2kb(payload)


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Terminal observation of one run of :class:`GraphRagBuilder`."""

    config_id: uuid.UUID
    build_id: uuid.UUID
    state: BuildState
    triples_written: int
    entities_written: int
    error: str | None


def _render_bundle_text(bundle: GraphRagBundle) -> str:
    lines: list[str] = ["GraphRAG context:"]
    if bundle.entities:
        lines.append("entities: " + ", ".join(bundle.entities))
    for r in bundle.relations:
        lines.append(f"({r.subject}) -[{r.relation} c={r.confidence:.2f}]-> ({r.object})")
    for ex in bundle.evidence_excerpts:
        lines.append(f"evidence: {ex}")
    return "\n".join(lines)


def _cap_to_2kb(payload: dict[str, Any]) -> dict[str, Any]:
    """Trim ``content`` so the serialised JSON fits in 2 KB (R11.06)."""
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) <= 2048:
        return payload
    content = payload["content"]
    # Binary search on length — shrink content until total fits.
    lo, hi = 0, len(content)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trimmed = dict(payload)
        trimmed["content"] = content[:mid] + "..."
        if len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8")) <= 2048:
            lo = mid
        else:
            hi = mid - 1
    trimmed = dict(payload)
    trimmed["content"] = content[:lo] + "..." if lo > 0 else "..."
    return trimmed


__all__ = [
    "BuildResult",
    "BuildState",
    "EntityHit",
    "GraphRagBundle",
    "GraphRagConfig",
    "GraphRagConfigDraft",
    "RelationEdge",
    "Triple",
]
