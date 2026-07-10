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
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_SECONDS_PER_DAY = 86_400.0


def recency_weighted_score(
    *,
    confidence: float | None,
    last_seen_at: float | None,
    half_life_days: float | None,
    now: float,
) -> float:
    """Recency-decayed ranking weight (R11.21, WS5 AC-9).

    ``confidence × exp(-ln(2) × Δt / halflife)`` where ``Δt`` is the age of the
    edge's ``last_seen_at`` (UTC epoch seconds) at ``now`` — the ``ln(2)``
    factor is what makes ``half_life_days`` an actual half-life: at
    ``Δt == halflife`` the decay factor is exactly 0.5, not ``exp(-1) ≈ 0.368``.
    Coalesces the same way NULL confidence already does in traversal: a missing
    ``confidence`` is 0.0, and a missing ``last_seen_at`` or half-life leaves the
    weight at pure confidence (decay factor 1.0) so legacy/timeless edges are
    never buried below zero. A recent low-confidence edge can outrank a stale
    high-confidence one when the half-life is short — the point of the temporal
    ranking.
    """
    conf = confidence if confidence is not None else 0.0
    if last_seen_at is None or not half_life_days or half_life_days <= 0:
        return conf
    age_seconds = max(0.0, now - last_seen_at)
    return conf * math.exp(-math.log(2) * age_seconds / (half_life_days * _SECONDS_PER_DAY))


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
    # Derived owning agent — the sole member of a singleton agent_group. ``None``
    # for a multi-member agent_group (Phase 2b WS1) or a chatroom/workspace owner
    # (WS2), which have no single owning agent.
    agent_id: uuid.UUID | None
    builder_key_group_id: uuid.UUID
    trigger_config: dict[str, Any]
    last_build_at: datetime | None
    last_build_state: BuildState
    last_build_error: str | None
    created_at: datetime
    deleted_at: datetime | None
    # Embedding pin (Phase 2a D2). The project's fixed (provider, model, dim);
    # ``None`` on a pre-2a row that has not yet self-pinned on a build.
    embed_provider: str | None = None
    embed_model: str | None = None
    embed_dim: int | None = None
    # Phase 2b — the discriminated owner (Phase 1 typed-FK). Exactly one owner_*
    # id is set for the ``owner_kind``. Phase 2a create only produces
    # ``agent_group`` owners; WS2 adds chatroom/workspace. Surfaced on the domain
    # config so the build delta-feed can scope per owner kind without a second
    # query.
    owner_kind: str = "agent_group"
    owner_chatroom_id: uuid.UUID | None = None
    owner_agent_group_id: uuid.UUID | None = None
    owner_workspace_id: uuid.UUID | None = None
    # Phase 2b WS5 (R11.21) — per-config recency half-life in days for temporal
    # retrieval decay. ``None`` inherits the platform default setting.
    recency_half_life_days: float | None = None
    # Phase 4α — the owner's display name, populated on the project-scoped read
    # paths (``_config_select``) so the Concept Maps overview can render it. Other
    # reads (turn resolver, membership feed) leave it ``None``.
    owner_name: str | None = None


@dataclass(frozen=True, slots=True)
class GraphRagConfigDraft:
    """Owner-centric create input (Phase 2b WS2).

    Replaces the Phase 1 agent-centric draft: a Concept Map is created for a
    discriminated owner (``agent_group`` — a group managed via the member-CRUD
    surface — or a ``chatroom`` / ``workspace``), not by auto-wrapping a single
    agent. ``owner_id`` is that owner entity's id; the service validates it lives
    in the config's project.
    """

    owner_kind: str
    owner_id: uuid.UUID
    builder_key_group_id: uuid.UUID
    trigger_config: dict[str, Any] = field(default_factory=dict)
    # Phase 2b WS5 (R11.21) — optional per-config recency half-life in days;
    # ``None`` leaves the config on the platform default. Validated > 0.
    recency_half_life_days: float | None = None


@dataclass(frozen=True, slots=True)
class AgentConceptMapCoverage:
    """One Concept Map covering an agent, for the read-only coverage view (R11.09).

    Phase 4α transparency (not an attach): every map that could feed an agent's
    retrieval — the maps of its chatrooms, the agent_groups it is a member of, and
    those chatrooms' workspaces. ``active`` mirrors the turn resolver's inclusion
    rule: a chatroom map is always active (inherits the room ACL), while a wide
    (agent_group / workspace) map is active only when its owner has
    ``concept_map_enabled``. An inactive entry exists but contributes nothing to
    retrieval until a Project Owner enables it.
    """

    config: GraphRagConfig
    owner_name: str
    active: bool


@dataclass(frozen=True, slots=True)
class ConceptMapOwnerOption:
    """A selectable owner for creating a Concept Map (Phase 4α overview picker).

    An owner (agent_group / chatroom / workspace) in the project that does not yet
    own a live Concept Map — the per-owner 1:1 means an owner with a map is not a
    valid create target and is excluded.
    """

    owner_kind: str
    owner_id: uuid.UUID
    owner_name: str


# Opaque evidence references (chat message ids today, document chunk refs for a
# Knowledge Map later) are length-bounded so a hallucinated or malformed extractor
# value never persists into Neo4j or crowds out real relations in the 2 KB bundle.
MAX_EVIDENCE_REF_LEN = 128


def normalize_evidence_refs(raw: object) -> tuple[str, ...]:
    """Coerce raw evidence values from LLM output or a Neo4j row into clean refs.

    Accepts a list/tuple of values; keeps only non-blank strings no longer than
    :data:`MAX_EVIDENCE_REF_LEN`. Non-strings (a JSON ``null`` decodes to
    ``None``, numbers, nested arrays) are dropped rather than coerced — this is
    the guard the WS3 opaque-string migration must keep so ``str(None)`` never
    becomes the literal ref ``"None"``. No UUID format is imposed, so a document
    Knowledge Map can reuse the same graph with chunk references.
    """
    if not isinstance(raw, list | tuple):
        return ()
    refs: list[str] = []
    for v in raw:
        if not isinstance(v, str):
            continue
        text = v.strip()
        if text and len(text) <= MAX_EVIDENCE_REF_LEN:
            refs.append(text)
    return tuple(refs)


@dataclass(frozen=True, slots=True)
class Triple:
    """A single extracted relation (R11.03).

    ``evidence_refs`` carries opaque source references that justify the triple
    so downstream retrieval can surface evidence excerpts. For a conversation
    Concept Map each ref is a chat message id string; the neutral ``str`` type
    lets a document Knowledge Map reuse the same graph with chunk references.

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
    evidence_refs: tuple[str, ...]
    subject_type: str = ""
    object_type: str = ""
    # Phase 2b (R11.22) — the agent_group member(s) whose messages contributed
    # this relation, derived from the evidence messages' source member (never
    # from LLM output). Accumulated as a set: two members independently stating
    # the same relation both appear, so a member-scoped retrieval filter returns
    # each member's true contributions. Empty for a non-agent_group owner or a
    # relation with no resolvable member provenance.
    source_member_ids: tuple[str, ...] = ()
    # Phase 2b WS5 (R11.21) — the earliest/latest source-message timestamps
    # (UTC epoch seconds) this relation was seen at, derived from the evidence
    # messages' ``created_at`` (never from LLM output). ``None`` when no evidence
    # resolves to a timestamp. Neo4j MERGE keeps first_seen earliest, last_seen
    # latest across delta builds; retrieval decays ranking by ``last_seen_at``.
    first_seen_at: float | None = None
    last_seen_at: float | None = None


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
    evidence_refs: tuple[str, ...]
    # Phase 2b (R11.22) — the contributing agent_group member(s), mirrored from
    # the Neo4j edge so a member-scoped retrieval can filter. Empty for a
    # non-agent_group / legacy edge.
    source_member_ids: tuple[str, ...] = ()
    # Phase 2b WS5 (R11.21) — earliest/latest source-message timestamps (UTC
    # epoch seconds) carried from the Neo4j edge, and ``score`` the recency-
    # weighted rank the retrieve service computes (``confidence × exp(-Δt /
    # halflife)`` on ``last_seen_at``). ``None`` on a legacy/timeless edge, which
    # then ranks on pure confidence.
    first_seen_at: float | None = None
    last_seen_at: float | None = None
    score: float | None = None


def edge_rank(edge: RelationEdge) -> float:
    """Sort key for a traversal edge: the recency-weighted ``score`` when the
    retrieve service computed one (WS5 AC-9), else raw ``confidence`` for a
    legacy/timeless edge. Single source of truth for both the retrieve-service
    sort and the WS4 layer-merge tiering."""
    return float(edge.score if edge.score is not None else edge.confidence)


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
    "MAX_EVIDENCE_REF_LEN",
    "BuildResult",
    "BuildState",
    "EntityHit",
    "GraphRagBundle",
    "GraphRagConfig",
    "GraphRagConfigDraft",
    "RelationEdge",
    "Triple",
    "edge_rank",
    "normalize_evidence_refs",
    "recency_weighted_score",
]
